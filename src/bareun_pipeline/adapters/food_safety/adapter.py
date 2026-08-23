# ==============================================================================
# 파일 4: src/bareun_pipeline/adapters/food_safety/adapter.py
# ==============================================================================
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from ...domain.record import CertRecord
from ...observability.logging import get_logger
from ..base import BaseAdapter
from .client import FoodSafetyClient
from .mapper import map_to_cert_records

logger = get_logger(__name__)


class FoodSafetyAdapter(BaseAdapter):
    """
    식품안심업소(위생등급) 어댑터
    
    멀티 벤더 페일오버 전략:
    1순위: data.go.kr 미러
    2순위: 식약처 원본 (MFDS)
    3순위: 로컬 원본 리플레이 (Bronze Layer)
    """

    source = 'food_safety'
    cert_type = 'food_safety'
    quota_provider = None  # 일일 제한이 넉넉하거나 별도 관리

    # PII 필드 정의 (Bronze 아카이빙 시 자동 마스킹 대상)
    pii_fields = {'PRSDNT_NM', 'WRKR_REG_NO'}

    def __init__(self, snapshot_first: bool = False):
        super().__init__()
        self.client = FoodSafetyClient(
            settings=self.settings,
            api_caller=self._safe_request,
        )
        self.snapshot_first = snapshot_first

    def collect(self, province: Optional[str] = None) -> list[CertRecord]:
        """데이터 수집 (BaseAdapter 시그니처 준수)"""
        if not self.settings.foodsafety_korea_key and not self.settings.data_go_kr_key:
            logger.warning('food_safety_skip_no_api_key')
            return []

        logger.info('food_safety_collect_start', province=province)

        # 1. 스냅샷 모드 (로컬 리플레이 우선)
        if self.snapshot_first and province:
            records = self._replay_from_raw(province)
            if records:
                logger.info('food_safety_snapshot_loaded', count=len(records), province=province)
                return records

        # 2. 벤더 전략 수립
        strategies = []
        env_url = os.getenv('FOOD_SAFETY_DATAGO_URL', '').strip()
        if self.client.datago_key and env_url:
            strategies.append(('datago', env_url))
        if self.client.mfds_key:
            strategies.append(('mfds', None))

        if not strategies:
            logger.error('food_safety_no_strategies_available')
            return []

        # 3. 벤더 간 경쟁 (Race) 실행 - 먼저 성공한 쪽 채택
        logger.info('food_safety_vendor_race_start', vendors=[s[0] for s in strategies])

        with ThreadPoolExecutor(max_workers=len(strategies)) as executor:
            futures = {
                executor.submit(self._execute_vendor, kind, url, province): kind
                for kind, url in strategies
            }

            for future in as_completed(futures):
                vendor_kind = futures[future]
                try:
                    rows = future.result()
                    if rows:
                        # 성공 시 다른 벤더 작업 취소 및 결과 반환
                        self.raw_rows = rows
                        records = map_to_cert_records(rows, province, self.source, self.cert_type)
                        
                        logger.info(
                            'food_safety_vendor_success',
                            vendor=vendor_kind,
                            count=len(records),
                            raw_count=len(rows),
                        )
                        
                        # 나머지 벤더 취소
                        for f in futures:
                            if not f.done():
                                f.cancel()
                                
                        return records
                        
                except Exception as e:
                    logger.error(
                        'food_safety_vendor_failed',
                        vendor=vendor_kind,
                        error=str(e),
                        error_type=type(e).__name__,
                    )

        # 4. 모든 벤더 실패 시 로컬 리플레이 (Fallback)
        logger.warning('food_safety_all_vendors_failed_trying_replay')
        records = self._replay_from_raw(province)
        if records:
            logger.info('food_safety_replay_success', count=len(records))
            return records

        logger.error('food_safety_all_failed')
        return []

    def _execute_vendor(self, kind: str, url: Optional[str], province: Optional[str]) -> list[dict]:
        """벤더별 수집 실행"""
        if kind == 'datago' and url:
            return self.client.fetch_datago(url)
        elif kind == 'mfds':
            return self.client.fetch_mfds()
        return []

    def _replay_from_raw(self, province: Optional[str]) -> list[CertRecord]:
        """로컬 원본 파일(Bronze)에서 리플레이"""
        try:
            from ...bronze.raw_archiver import RawArchiver
            archiver = RawArchiver()
            rows = archiver.load_latest(self.source, province or 'nationwide')
            if not rows:
                return []
            return map_to_cert_records(rows, province, self.source, self.cert_type)
        except Exception as e:
            logger.error('food_safety_replay_error', error=str(e))
            return []