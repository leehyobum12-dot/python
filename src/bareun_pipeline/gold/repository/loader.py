"""
Bareun Pipeline: Gold Repository Orchestrator
==============================================
- CertRecord 배치 적재 오케스트레이션
- 트랜잭션 경계 관리 및 부분 실패 대응
- 기존 gold/supabase_loader.py 진입점 호환

위치: src/bareun_pipeline/gold/repository/loader.py.py
"""
from __future__ import annotations

from typing import Optional

from ...domain.record import CertRecord
from ...observability.logging import get_logger
from .client import SupabaseClient, SupabaseClientError
from .mapper import to_db_rows

logger = get_logger(__name__)

class SupabaseLoader:
    """
    Gold Layer Supabase 적재 오케스트레이터
    
    - 배치 분할 및 순차 처리
    - 부분 실패 시 로깅 및 계속 진행 (Partial Success)
    - 멱등성 보장 (dedup_key 기반 Upsert)
    """

    def __init__(self, client: Optional[SupabaseClient] = None):
        self.client = client or SupabaseClient()

    def load_batch(
        self,
        records: list[CertRecord],
        batch_size: int = 100,
    ) -> int:
        """
        배치 적재 수행
        
        Args:
            records: 적재할 CertRecord 목록
            batch_size: 배치 크기 (네트워크 왕복 최소화)
            
        Returns:
            성공적으로 적재된 레코드 수
        """
        if not records:
            return 0

        total_success = 0
        total_failed = 0

        for i in range(0, len(records), batch_size):
            batch = records[i:i + batch_size]
            
            try:
                # 1. 도메인 모델 → DB Row 변환 (순수 함수)
                rows = to_db_rows(batch)
                
                # 2. Supabase Upsert (I/O 전담 클라이언트)
                success_count = self.client.upsert_rows(rows)
                total_success += success_count
                
                logger.info(
                    "supabase_load_batch_success",
                    batch_size=len(batch),
                    progress=f"{i + len(batch)}/{len(records)}",
                )
                
            except SupabaseClientError as e:
                total_failed += len(batch)
                logger.error("supabase_load_batch_failed", batch_start=i, error=str(e))
                
                if "Circuit breaker is open" in str(e):
                    logger.critical("supabase_circuit_open_abort", remaining_records=len(records) - i)
                    break
                    
            except Exception as e:
                total_failed += len(batch)
                logger.error(
                    "supabase_load_batch_unexpected_error",
                    batch_start=i, error=str(e), error_type=type(e).__name__,
                )

        logger.info("supabase_load_complete", total=len(records), success=total_success, failed=total_failed)
        return total_success

# ═══════════════════════════════════════════════════════════
# Legacy Compatibility (기존 함수 시그니처 유지)
# ═══════════════════════════════════════════════════════════
def load_to_supabase(records: list[CertRecord]) -> int:
    """기존 진입점 호환용 래퍼"""
    loader = SupabaseLoader()
    return loader.load_batch(records)