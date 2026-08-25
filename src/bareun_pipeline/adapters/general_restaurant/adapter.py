# ==============================================================================
# 파일 4: src/bareun_pipeline/adapters/general_restaurant/adapter.py
# ==============================================================================
"""일반음식점 어댑터 오케스트레이터"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from ...domain.record import CertRecord
from ...observability.logging import get_logger
from ..base import BaseAdapter, match_prefixes
from .client import GeneralRestaurantClient
from .mapper import map_item_to_record

logger = get_logger(__name__)


class GeneralRestaurantAdapter(BaseAdapter):
    source = 'general_restaurant'
    cert_type = 'general_restaurant'
    quota_provider = 'general_restaurant'
    pii_fields = set()

    PAGE_SIZE = 500
    MAX_WORKERS = 4
    MAX_PAGES_DEFAULT = 100

    def __init__(self, max_pages: int = MAX_PAGES_DEFAULT):
        super().__init__()
        self.client = GeneralRestaurantClient(
            service_key=self.settings.data_go_kr_key or '', api_caller=self._safe_request
        )
        self.max_pages = max_pages

    def collect(self, province: str | None = None) -> list[CertRecord]:
        if not self.client.service_key:
            logger.warning('general_restaurant_skip_no_api_key')
            return []

        prefixes = match_prefixes(province) if province else ()
        logger.info('general_restaurant_collect_start', province=province, max_pages=self.max_pages)

        first_items, total = self.client.fetch_page(1, self.PAGE_SIZE)
        if not first_items and total == 0:
            return []

        records = self._process_items(first_items, prefixes)
        self.raw_rows.extend(first_items)

        total_pages = min((total // self.PAGE_SIZE) + 1, self.max_pages)
        if total_pages <= 1:
            return [r for r in records if r.is_valid()]

        pages = list(range(2, total_pages + 1))
        with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as executor:
            futures = {executor.submit(self.client.fetch_page, p, self.PAGE_SIZE): p for p in pages}
            for future in as_completed(futures):
                page = futures[future]
                try:
                    items, _ = future.result()
                    records.extend(self._process_items(items, prefixes))
                    self.raw_rows.extend(items)
                except Exception as e:
                    logger.error('general_restaurant_page_failed', page=page, error=str(e))
                time.sleep(0.2)

        valid = [r for r in records if r.is_valid()]
        logger.info('general_restaurant_collect_complete', count=len(valid), total=total)
        return valid

    def _process_items(self, items: list[dict], prefixes: tuple) -> list[CertRecord]:
        results = []
        for item in items:
            if not isinstance(item, dict):
                continue
            record = map_item_to_record(item, self.source, self.cert_type, prefixes)
            if record:
                results.append(record)
        return results
