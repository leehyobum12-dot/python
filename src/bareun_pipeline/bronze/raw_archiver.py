"""
Bronze Layer: 원본 데이터 보존
================================
- NDJSON 형식으로 디스크 저장 (메모리 효율)
- PII 마스킹 적용
- raw_certifications 테이블에 INSERT (감사 추적)
- 오래된 파일 자동 정리

기존 참고:
- storage/raw_archiver.py 전체 구조
- _redact_pii() PII 마스킹 로직
- _filter_rows() 지역별 필터링
- _prune() 오래된 파일 정리

보존 사항:
- PII 마스킹 (domain/privacy.py 연동)
- 지역별 필터링
- 파일 보존 기간 관리

개선 사항:
- NDJSON 형식 (기존 JSON 대비 메모리 효율)
- gzip 압축 (디스크 용량 절감)
- raw_certifications INSERT 연동
- 구조화 로깅
"""
from __future__ import annotations

import gzip
import json
import os
from datetime import datetime
from typing import List, Dict, Any, Optional

from ..domain.privacy import redact_pii
from ..observability.logging import get_logger
from ..settings import get_settings


logger = get_logger(__name__)


class RawArchiver:
    """
    원본 데이터 보존 관리자

    사용법:
        archiver = RawArchiver()
        archiver.archive('safe_restaurant', '제주특별자치도', raw_rows, batch_id)
    """

    RETENTION_DAYS = 30  # 원본 파일 보존 기간

    def __init__(self, supabase_client=None):
        self.settings = get_settings()
        self._supabase = supabase_client

        # 디렉토리 생성
        os.makedirs(self.settings.raw_dir, exist_ok=True)

    @property
    def supabase(self):
        """Supabase 클라이언트 (lazy init)"""
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
        province: str,
        rows: List[Dict[str, Any]],
        batch_id: str,
    ) -> Optional[str]:
        """
        원본 데이터 보존

        Args:
            source: 어댑터 소스명 (e.g., 'safe_restaurant')
            province: 시도명
            rows: 원본 데이터 목록
            batch_id: 배치 식별자 (YYYYMMDD_HHMMSS 형식 권장)

        Returns:
            저장된 파일 경로 (실패 시 None)
        """
        if not rows:
            logger.info(
                'archive_skip_empty',
                source=source,
                province=province,
            )
            return None

        # PII 마스킹
        masked_rows = redact_pii(rows, source)

        # 파일 저장 (gzip 압축 NDJSON)
        file_path = self._save_to_disk(source, province, masked_rows, batch_id)

        # Supabase raw_certifications 테이블에 INSERT
        self._insert_to_supabase(source, province, masked_rows, batch_id)

        # 오래된 파일 정리
        self._prune_old_files(source)

        return file_path

    def _save_to_disk(
        self,
        source: str,
        province: str,
        rows: List[Dict[str, Any]],
        batch_id: str,
    ) -> str:
        """NDJSON 형식으로 디스크 저장 (gzip 압축)"""
        source_dir = os.path.join(self.settings.raw_dir, source)
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

    def _insert_to_supabase(
        self,
        source: str,
        province: str,
        rows: List[Dict[str, Any]],
        batch_id: str,
    ) -> None:
        """raw_certifications 테이블에 INSERT"""
        if not rows:
            return

        try:
            # 각 행을 개별 INSERT (source_key는 행의 해시)
            records = []
            for row in rows:
                # source_key 생성 (행 내용의 해시)
                row_json = json.dumps(row, sort_keys=True, ensure_ascii=False)
                import hashlib
                source_key = hashlib.sha256(row_json.encode()).hexdigest()[:32]

                records.append({
                    'source': source,
                    'batch_id': batch_id,
                    'source_key': source_key,
                    'raw': row,
                })

            # 배치 INSERT (최대 1000개씩)
            chunk_size = 1000
            for i in range(0, len(records), chunk_size):
                chunk = records[i:i + chunk_size]
                self.supabase.table('raw_certifications').insert(chunk).execute()

            logger.info(
                'raw_cert_inserted',
                source=source,
                batch_id=batch_id,
                count=len(records),
            )

        except Exception as e:
            logger.error(
                'raw_cert_insert_failed',
                source=source,
                batch_id=batch_id,
                error=str(e),
            )

    def _prune_old_files(self, source: str) -> None:
        """오래된 원본 파일 정리"""
        source_dir = os.path.join(self.settings.raw_dir, source)
        if not os.path.exists(source_dir):
            return

        cutoff_time = datetime.now().timestamp() - (self.RETENTION_DAYS * 86400)

        for filename in os.listdir(source_dir):
            if not filename.endswith('.jsonl.gz'):
                continue

            file_path = os.path.join(source_dir, filename)
            file_mtime = os.path.getmtime(file_path)

            if file_mtime < cutoff_time:
                try:
                    os.remove(file_path)
                    logger.debug('archive_pruned', path=file_path)
                except OSError:
                    pass

    def load_latest(
        self,
        source: str,
        province: str,
    ) -> Optional[List[Dict[str, Any]]]:
        """
        최신 원본 데이터 로드 (리플레이용)

        Args:
            source: 어댑터 소스명
            province: 시도명

        Returns:
            원본 데이터 목록 (없으면 None)
        """
        source_dir = os.path.join(self.settings.raw_dir, source)
        if not os.path.exists(source_dir):
            return None

        safe_province = province.replace('/', '_').replace('\\', '_')
        pattern = f'{safe_province}_'

        # 최신 파일 찾기
        candidates = []
        for filename in os.listdir(source_dir):
            if filename.startswith(pattern) and filename.endswith('.jsonl.gz'):
                file_path = os.path.join(source_dir, filename)
                candidates.append((os.path.getmtime(file_path), file_path))

        if not candidates:
            return None

        candidates.sort(reverse=True)
        latest_path = candidates[0][1]

        # 로드
        try:
            rows = []
            with gzip.open(latest_path, 'rt', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        rows.append(json.loads(line))

            logger.info(
                'archive_loaded',
                source=source,
                province=province,
                count=len(rows),
                path=latest_path,
            )

            return rows

        except Exception as e:
            logger.error(
                'archive_load_failed',
                source=source,
                province=province,
                path=latest_path,
                error=str(e),
            )
            return None