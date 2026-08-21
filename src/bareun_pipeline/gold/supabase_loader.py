"""
Gold Layer: Supabase 적재
===========================
- bulk_upsert_restaurants RPC 호출
- 동적 청크 크기 (payload 크기 기반)
- 재시도 로직 (지수 백오프)

안정성 개선:
- 동적 청크 크기로 Supabase 타임아웃 방지
- payload 크기에 따라 청크 크기 자동 조정
"""
from __future__ import annotations

import json
import time
from typing import List, Dict, Any, Optional

from supabase import create_client, Client

from .transform import GoldRestaurant
from ..observability.logging import get_logger
from ..settings import get_settings


logger = get_logger(__name__)


class SupabaseLoader:
    """
    Supabase bulk_upsert 로더

    사용법:
        loader = SupabaseLoader()
        result = loader.load(gold_records)

    안정성:
        동적 청크 크기로 Supabase 타임아웃 방지
    """

    DEFAULT_CHUNK_SIZE = 500  # 기본 청크 크기
    MIN_CHUNK_SIZE = 100      # 최소 청크 크기
    MAX_CHUNK_SIZE = 1000     # 최대 청크 크기
    MAX_RETRIES = 3           # 최대 재시도 횟수

    # Supabase PostgREST의 안전한 payload 임계값 (5MB)
    MAX_PAYLOAD_SIZE = 5 * 1024 * 1024

    def __init__(self, supabase_client: Optional[Client] = None):
        self.settings = get_settings()
        self._supabase = supabase_client

    @property
    def supabase(self) -> Client:
        """Supabase 클라이언트 (lazy init)"""
        if self._supabase is None:
            self._supabase = create_client(
                self.settings.supabase_url,
                self.settings.supabase_service_role_key,
            )
        return self._supabase

    def _calculate_chunk_size(self, records: List[GoldRestaurant]) -> int:
        """
        payload 크기에 따른 동적 청크 크기 계산

        Supabase PostgREST의 최대 payload 크기를 고려하여
        안전한 청크 크기를 동적으로 계산합니다.

        Args:
            records: 적재할 레코드 목록

        Returns:
            계산된 청크 크기
        """
        if not records:
            return self.DEFAULT_CHUNK_SIZE

        # 첫 번째 레코드의 payload 크기 추정
        sample_payload = records[0].to_payload()
        sample_size = len(
            json.dumps(sample_payload, ensure_ascii=False).encode('utf-8')
        )

        if sample_size == 0:
            return self.DEFAULT_CHUNK_SIZE

        # 안전한 청크 크기 계산
        estimated_chunk = self.MAX_PAYLOAD_SIZE // sample_size

        # 최소/최대 범위 제한
        chunk_size = max(self.MIN_CHUNK_SIZE, min(estimated_chunk, self.MAX_CHUNK_SIZE))

        logger.debug(
            'dynamic_chunk_size_calculated',
            sample_size=sample_size,
            estimated_chunk=estimated_chunk,
            final_chunk_size=chunk_size,
        )

        return chunk_size

    def load(self, records: List[GoldRestaurant]) -> Dict[str, Any]:
        """
        Gold 레코드를 Supabase에 적재

        Args:
            records: GoldRestaurant 목록

        Returns:
            적재 결과 요약
        """
        if not records:
            logger.warning('supabase_load_skip_empty')
            return {'loaded': 0, 'chunks': 0, 'errors': []}

        logger.info('supabase_load_start', count=len(records))

        total_affected = 0
        errors: List[Dict[str, Any]] = []

        # 동적 청크 크기 계산
        chunk_size = self._calculate_chunk_size(records)

        # 청크 단위로 분할
        chunks = [
            records[i:i + chunk_size]
            for i in range(0, len(records), chunk_size)
        ]

        logger.info(
            'supabase_load_chunks_prepared',
            chunk_size=chunk_size,
            total_chunks=len(chunks),
        )

        for chunk_idx, chunk in enumerate(chunks, 1):
            logger.info(
                'supabase_load_chunk',
                chunk=chunk_idx,
                total_chunks=len(chunks),
                records=len(chunk),
            )

            try:
                affected = self._load_chunk(chunk)
                total_affected += affected

                logger.info(
                    'supabase_load_chunk_success',
                    chunk=chunk_idx,
                    affected=affected,
                )

            except Exception as e:
                logger.error(
                    'supabase_load_chunk_failed',
                    chunk=chunk_idx,
                    error=str(e),
                    error_type=type(e).__name__,
                )
                errors.append({
                    'chunk': chunk_idx,
                    'error': str(e),
                })

        # ANALYZE 실행 (인덱스 통계 갱신)
        try:
            self._analyze()
            logger.info('supabase_analyze_complete')
        except Exception as e:
            logger.warning('supabase_analyze_failed', error=str(e))

        result = {
            'loaded': total_affected,
            'chunks': len(chunks),
            'errors': errors,
        }

        logger.info(
            'supabase_load_complete',
            loaded=total_affected,
            chunks=len(chunks),
            error_count=len(errors),
        )

        return result

    def _load_chunk(self, chunk: List[GoldRestaurant]) -> int:
        """
        단일 청크 적재 (재시도 포함)

        Args:
            chunk: 적재할 청크

        Returns:
            affected row count
        """
        payload = [record.to_payload() for record in chunk]

        last_error: Optional[Exception] = None

        for attempt in range(self.MAX_RETRIES):
            try:
                response = self.supabase.rpc(
                    'bulk_upsert_restaurants',
                    {'payload': payload},
                ).execute()

                affected = response.data
                if isinstance(affected, (int, float)):
                    return int(affected)
                elif isinstance(affected, str) and affected.isdigit():
                    return int(affected)
                else:
                    return 0

            except Exception as e:
                last_error = e

                if attempt < self.MAX_RETRIES - 1:
                    wait = 2 ** attempt
                    logger.warning(
                        'supabase_load_retry',
                        attempt=attempt + 1,
                        wait=wait,
                        error=str(e),
                    )
                    time.sleep(wait)
                else:
                    raise

        if last_error is not None:
            raise last_error
        raise RuntimeError('Unexpected error: no exception captured during retries')

    def _analyze(self) -> None:
        """ANALYZE 실행"""
        self.supabase.rpc('analyze_restaurants').execute()


def load_records(records: List[GoldRestaurant]) -> Dict[str, Any]:
    """레코드 적재 (편의 함수)"""
    loader = SupabaseLoader()
    return loader.load(records)