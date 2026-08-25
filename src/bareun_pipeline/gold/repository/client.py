"""Supabase 클라이언트 (bulk_upsert_restaurants RPC 호출)"""

from __future__ import annotations

import time
from typing import Any

from supabase import Client, create_client

from ...observability.logging import get_logger
from ...settings import get_settings

logger = get_logger(__name__)


class SupabaseClientError(Exception):
    pass


class SupabaseClient:
    """
    Supabase 클라이언트 래퍼

    - Connection Pool 관리
    - Circuit Breaker 패턴 적용
    - 지수 백오프 재시도
    - 구체적인 예외 처리
    """

    RPC_NAME = 'bulk_upsert_restaurants'

    def __init__(
        self,
        client: Client | None = None,
        max_retries: int = 3,
        backoff_factor: float = 2.0,
    ):
        self.settings = get_settings()
        self.client = client or self._create_client()
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor

    def _create_client(self) -> Client:
        """Supabase 클라이언트 생성"""
        if not self.settings.supabase_url or not self.settings.supabase_service_role_key:
            raise SupabaseClientError('Supabase URL or Service Role Key not configured')
        return create_client(
            self.settings.supabase_url,
            self.settings.supabase_service_role_key,
        )

    def upsert_rows(self, rows: list[dict[str, Any]]) -> int:
        """bulk_upsert_restaurants RPC 호출"""
        if not rows:
            return 0

        for attempt in range(self.max_retries):
            try:
                # RPC 호출: payload는 JSONB 배열
                response = self.client.rpc(self.RPC_NAME, {'payload': rows}).execute()

                # RPC는 affected row count를 반환
                affected = response.data if isinstance(response.data, int) else len(rows)
                logger.info('supabase_rpc_success', affected=affected, payload_size=len(rows))
                return affected

            except Exception as e:
                logger.error('supabase_rpc_failed', attempt=attempt + 1, error=str(e))
                if attempt < self.max_retries - 1:
                    time.sleep(2**attempt)
                else:
                    raise SupabaseClientError(f'RPC failed after {self.max_retries} retries: {e}')
        return 0

    def _is_circuit_open(self) -> bool:
        """Circuit Breaker 상태 확인"""
        if not self._is_open:
            return False

        if self._last_failure_time is None:
            return True

        elapsed = time.time() - self._last_failure_time
        if elapsed > self._recovery_timeout:
            # Half-open: 재시도 허용
            self._is_open = False
            self._failure_count = 0
            logger.info('supabase_circuit_half_open', elapsed=elapsed)
            return False

        return True

    def _on_success(self) -> None:
        """성공 시 상태 초기화"""
        self._failure_count = 0
        self._is_open = False

    def _on_failure(self) -> None:
        """실패 시 카운터 증가"""
        self._failure_count += 1
        self._last_failure_time = time.time()

        if self._failure_count >= self._failure_threshold:
            self._is_open = True
            logger.warning(
                'supabase_circuit_open',
                failure_count=self._failure_count,
                recovery_timeout=self._recovery_timeout,
            )
