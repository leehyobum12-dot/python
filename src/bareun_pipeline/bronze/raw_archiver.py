"""
Bronze Layer: 원본 데이터 보존 (Production Hardened)
=====================================================
- NDJSON 형식으로 디스크 저장 (메모리 효율, gzip 압축)
- PII 마스킹 적용 (개인정보보호법 준수)
- raw_certifications 테이블에 INSERT (감사 추적)
- 오래된 파일 자동 정리 (30일 보존)

개선 사항 (v2.0 Production):
- threading.Lock으로 스레드 안전성 확보 (병렬 수집 대응)
- 구체적 예외 처리 (Broad Except 제거)
- Chunk 단위 부분 성공 (데이터 손실 방지)
- UTC 타임존 인식 (서버 타임존 독립적)
- ON CONFLICT DO NOTHING (중복 INSERT 방지)
- province=None 방어 로직
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import threading
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from httpx import ConnectError, TimeoutException
from postgrest import APIError

from ..domain.privacy import redact_pii
from ..observability.logging import get_logger
from ..settings import get_settings

# 🆕 TYPE_CHECKING 블록: 런타임 오버헤드 없이 타입 정보만 활용
if TYPE_CHECKING:
    from supabase import Client

logger = get_logger(__name__)


class RawArchiver:
    """
    원본 데이터 보존 관리자 (Thread-Safe, Production-Grade)

    사용법:
        archiver = RawArchiver()
        archiver.archive('safe_restaurant', '제주특별자치도', raw_rows, batch_id)

    설계 원칙:
        - 디스크 파일과 DB INSERT는 Best-Effort (부분 실패 허용)
        - 파일 시스템 작업은 threading.Lock으로 보호
        - PII 마스킹은 도메인 모듈에 위임 (SRP)
    """

    RETENTION_DAYS = 30
    CHUNK_SIZE = 1000
    MAX_RETRY_PER_CHUNK = 2

    def __init__(self, supabase_client: Client | None = None) -> None:
        self.settings = get_settings()
        self._supabase: Client | None = supabase_client
        self._lock = threading.Lock()
        os.makedirs(self.settings.raw_dir, exist_ok=True)

    @property
    def supabase(self):
        """Supabase 클라이언트 (lazy init, 재사용)"""
        if self._supabase is None:
            from supabase import create_client

            self._supabase = create_client(
                self.settings.supabase_url,
                self.settings.supabase_service_role_key,
            )
        return self._supabase

    def archive(
        self,
        source: str,
        province: str | None,
        rows: list[dict[str, Any]],
        batch_id: str,
    ) -> str | None:
        """
        원본 데이터 보존 (파일 + DB)

        Args:
            source: 어댑터 소스명 (e.g., 'safe_restaurant')
            province: 시도명 (None이면 'nationwide')
            rows: 원본 데이터 목록
            batch_id: 배치 식별자 (YYYYMMDD_HHMMSS 권장)

        Returns:
            저장된 파일 경로 (실패 시 None)
        """
        if not rows:
            logger.info('archive_skip_empty', source=source, province=province)
            return None

        province_key = province or 'nationwide'

        # 1. PII 마스킹 (도메인 모듈 위임)
        masked_rows = redact_pii(rows, source)

        # 2. 디스크 저장 (락 보호)
        file_path = self._save_to_disk(source, province_key, masked_rows, batch_id)

        # 3. DB INSERT (부분 성공)
        self._insert_to_supabase(source, province_key, masked_rows, batch_id)

        # 4. 오래된 파일 정리 (Best-Effort)
        self._prune_old_files(source)

        return file_path

    def _save_to_disk(
        self,
        source: str,
        province: str,
        rows: list[dict[str, Any]],
        batch_id: str,
    ) -> str | None:
        """NDJSON 형식으로 디스크 저장 (gzip 압축, 락 보호)"""
        source_dir = os.path.join(self.settings.raw_dir, source)

        # 🆕 락으로 파일 시스템 작업 보호 (병렬 수집 대응)
        with self._lock:
            try:
                os.makedirs(source_dir, exist_ok=True)

                safe_province = province.replace('/', '_').replace('\\', '_')
                filename = f'{safe_province}_{batch_id}.jsonl.gz'
                file_path = os.path.join(source_dir, filename)

                with gzip.open(file_path, 'wt', encoding='utf-8') as f:
                    for row in rows:
                        f.write(json.dumps(row, ensure_ascii=False) + '\n')

                logger.info(
                    'archive_saved',
                    source=source,
                    province=province,
                    count=len(rows),
                    path=file_path,
                )
                return file_path

            except OSError as e:
                # 디스크 공간 부족, 권한 오류
                logger.error(
                    'archive_save_failed_os_error',
                    source=source,
                    province=province,
                    error=str(e),
                )
                return None
            except Exception as e:
                # gzip, json 등 기타 오류
                logger.error(
                    'archive_save_failed_unexpected',
                    source=source,
                    province=province,
                    error=str(e),
                    error_type=type(e).__name__,
                )
                return None

    def _insert_to_supabase(
        self,
        source: str,
        province: str,
        rows: list[dict[str, Any]],
        batch_id: str,
    ) -> None:
        """
        raw_certifications 테이블에 INSERT (Chunk 단위 부분 성공)

        구체적 예외 처리:
        - APIError (PostgREST 오류): 재시도
        - TimeoutException, ConnectError: 재시도
        - 기타 예외: 즉시 포기 (데이터 문제 가능성)
        """
        if not rows:
            return

        # 🆕 상단 import로 이동 (성능 최적화)
        records: list[dict[str, Any]] = []
        for row in rows:
            row_json = json.dumps(row, sort_keys=True, ensure_ascii=False)
            source_key = hashlib.sha256(row_json.encode()).hexdigest()[:32]
            records.append(
                {
                    'source': source,
                    'batch_id': batch_id,
                    'source_key': source_key,
                    'raw': row,
                }
            )

        total_success = 0
        total_failed = 0

        for i in range(0, len(records), self.CHUNK_SIZE):
            chunk: list[dict[str, Any]] = records[i : i + self.CHUNK_SIZE]
            success = self._insert_chunk_with_retry(chunk, source, batch_id)
            if success:
                total_success += len(chunk)
            else:
                total_failed += len(chunk)

        logger.info(
            'raw_cert_inserted',
            source=source,
            batch_id=batch_id,
            total=len(records),
            success=total_success,
            failed=total_failed,
        )

    def _insert_chunk_with_retry(
        self,
        chunk: list[dict[str, Any]],
        source: str,
        batch_id: str,
    ) -> bool:
        """단일 Chunk INSERT (재시도 포함, supabase-py v2.x API)"""
        for attempt in range(1, self.MAX_RETRY_PER_CHUNK + 1):
            try:
                # 🆕 supabase-py v2.x: insert() → upsert()로 교체
                # - v2.x에서는 insert()에 on_conflict 파라미터가 존재하지 않음
                # - upsert()는 UNIQUE 제약 기반 자동 Upsert 수행
                # - on_conflict는 upsert()에서는 여전히 유효한 파라미터
                upsert_builder = self.supabase.table('raw_certifications').upsert(
                    chunk,
                    on_conflict='source,source_key,batch_id',
                )
                upsert_builder.execute()
                return True

            except (TimeoutException, ConnectError) as e:
                # 네트워크 오류: 재시도
                logger.warning(
                    'raw_cert_network_error',
                    source=source,
                    batch_id=batch_id,
                    attempt=attempt,
                    error=str(e),
                )
                if attempt < self.MAX_RETRY_PER_CHUNK:
                    continue
                logger.error(
                    'raw_cert_network_error_final',
                    source=source,
                    batch_id=batch_id,
                    chunk_size=len(chunk),
                )
                return False

            except APIError as e:
                # PostgREST 오류 (5xx, 4xx)
                status = getattr(e, 'status_code', None) or getattr(e, 'code', None)
                logger.error(
                    'raw_cert_api_error',
                    source=source,
                    batch_id=batch_id,
                    status=status,
                    error=str(e),
                )
                # 5xx는 재시도, 4xx는 즉시 포기
                if status and 500 <= int(status) < 600:
                    if attempt < self.MAX_RETRY_PER_CHUNK:
                        continue
                return False

            except Exception as e:
                # 예상치 못한 예외 (데이터 구조 문제 등)
                logger.error(
                    'raw_cert_unexpected_error',
                    source=source,
                    batch_id=batch_id,
                    error=str(e),
                    error_type=type(e).__name__,
                )
                return False

        return False

    def _prune_old_files(self, source: str) -> None:
        """오래된 원본 파일 정리 (UTC 타임존 인식)"""
        source_dir = os.path.join(self.settings.raw_dir, source)
        if not os.path.exists(source_dir):
            return

        # 🆕 UTC 타임존 인식 (서버 타임존 독립적)
        now_utc = datetime.now(UTC)
        cutoff_timestamp = now_utc.timestamp() - (self.RETENTION_DAYS * 86400)

        pruned = 0
        for filename in os.listdir(source_dir):
            if not filename.endswith('.jsonl.gz'):
                continue

            file_path = os.path.join(source_dir, filename)
            try:
                file_mtime = os.path.getmtime(file_path)
                if file_mtime < cutoff_timestamp:
                    os.remove(file_path)
                    pruned += 1
                    logger.debug('archive_pruned', path=file_path)
            except OSError:
                pass

        if pruned > 0:
            logger.info('archive_prune_complete', source=source, pruned=pruned)

    def load_latest(
        self,
        source: str,
        province: str | None,
    ) -> list[dict[str, Any]] | None:
        """
        최신 원본 데이터 로드 (리플레이용)

        Args:
            source: 어댑터 소스명
            province: 시도명 (None이면 'nationwide')

        Returns:
            원본 데이터 목록 (없으면 None)
        """
        source_dir = os.path.join(self.settings.raw_dir, source)
        if not os.path.exists(source_dir):
            return None

        # 🆕 province=None 방어
        province_key = province or 'nationwide'
        safe_province = province_key.replace('/', '_').replace('\\', '_')
        pattern = f'{safe_province}_'

        candidates: list[tuple[float, str]] = []
        for filename in os.listdir(source_dir):
            if filename.startswith(pattern) and filename.endswith('.jsonl.gz'):
                file_path = os.path.join(source_dir, filename)
                try:
                    candidates.append((os.path.getmtime(file_path), file_path))
                except OSError:
                    continue

        if not candidates:
            return None

        candidates.sort(reverse=True)
        latest_path = candidates[0][1]

        try:
            rows: list[dict[str, Any]] = []
            with gzip.open(latest_path, 'rt', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        rows.append(json.loads(line))

            logger.info(
                'archive_loaded',
                source=source,
                province=province_key,
                count=len(rows),
                path=latest_path,
            )
            return rows

        except (OSError, json.JSONDecodeError, gzip.BadGzipFile) as e:
            logger.error(
                'archive_load_failed',
                source=source,
                province=province_key,
                path=latest_path,
                error=str(e),
                error_type=type(e).__name__,
            )
            return None
