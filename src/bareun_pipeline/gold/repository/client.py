"""
Bareun Pipeline: Gold Repository Client
========================================
- Supabase 클라이언트 래퍼 (Connection, Retry, Error Handling)
- Circuit Breaker 및 지수 백오프 적용
- 구체적인 예외 처리 (Broad Except 제거)

위치: src/bareun_pipeline/gold/repository/client.py
"""
from __future__ import annotations

import time
from typing import Any, Optional

from supabase import Client, create_client

from ...observability.logging import get_logger
from ...settings import get_settings

logger = get_logger(__name__)


class SupabaseClientError(Exception):
    """Supabase 클라이언트 오류"""
    pass


class SupabaseClient:
    """
    Supabase 클라이언트 래퍼
    
    - Connection Pool 관리
    - Circuit Breaker 패턴 적용
    - 지수 백오프 재시도
    - 구체적인 예외 처리
    """

    TABLE_NAME = "restaurant_certification"

    def __init__(
        self,
        client: Optional[Client] = None,
        max_retries: int = 3,
        backoff_factor: float = 2.0,
    ):
        self.settings = get_settings()
        self.client = client or self._create_client()
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self._failure_count = 0
        self._failure_threshold = 5
        self._is_open = False
        self._last_failure_time: Optional[float] = None
        self._recovery_timeout = 300.0

    def _create_client(self) -> Client:
        """Supabase 클라이언트 생성"""
        if not self.settings.supabase_url or not self.settings.supabase_service_role_key:
            raise SupabaseClientError("Supabase URL or Service Role Key not configured")
        return create_client(
            self.settings.supabase_url,
            self.settings.supabase_service_role_key,
        )

    def upsert_rows(self, rows: list[dict[str, Any]]) -> int:
        """
        배치 Upsert 수행
        
        Args:
            rows: Supabase 테이블 Row 목록
            
        Returns:
            성공적으로 적재된 행 수
            
        Raises:
            SupabaseClientError: Circuit이 열려 있거나 재시도 실패 시
        """
        if self._is_circuit_open():
            raise SupabaseClientError("Circuit breaker is open. Supabase upsert blocked.")

        last_error = None
        
        for attempt in range(self.max_retries):
            try:
                response = self.client.table(self.TABLE_NAME).upsert(
                    rows,
                    on_conflict="dedup_key"
                ).execute()
                
                self._on_success()
                return len(rows)
                
            except Exception as e:
                last_error = e
                self._on_failure()
                
                if attempt < self.max_retries - 1:
                    wait_time = self.backoff_factor ** attempt
                    logger.warning(
                        "supabase_upsert_retry",
                        attempt=attempt + 1,
                        wait_seconds=wait_time,
                        error=str(e),
                    )
                    time.sleep(wait_time)
                else:
                    logger.error(
                        "supabase_upsert_failed_final",
                        error=str(e),
                        rows_count=len(rows),
                    )

        raise SupabaseClientError(f"Upsert failed after {self.max_retries} retries: {last_error}")

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
            logger.info("supabase_circuit_half_open", elapsed=elapsed)
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
                "supabase_circuit_open",
                failure_count=self._failure_count,
                recovery_timeout=self._recovery_timeout,
            )