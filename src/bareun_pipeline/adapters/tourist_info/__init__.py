"""
Bareun Pipeline: Tourist Info Adapter (Orchestrator)
=====================================================
- 기존 660줄의 God Object를 대체하는 최종 진입점
- Client(HTTP) + Mapper(변환) + Checkpoint(상태) 조율

위치: src/bareun_pipeline/adapters/tourist_info/__init__.py
"""

from __future__ import annotations

import time
from typing import Optional

from ...bronze.tourist_checkpoint import TouristCheckpoint
from ...domain.record import CertRecord
from ...observability.logging import get_logger
from ..base import BaseAdapter
from .client import TouristInfoClient
from .mapper import map_to_record

logger = get_logger(__name__)


class TouristInfoAdapter(BaseAdapter):
    """국문 관광정보 서비스 어댑터 (음식점 특화)"""

    source = 'tourist_info'
    cert_type = 'tourist_info'
    quota_provider = 'tourist_info'
    pii_fields = set()

    PAGE_SIZE = 100
    DETAIL_DELAY = 0.5

    def __init__(
        self,
        fetch_details: bool = True,
        max_detail_count: int = 30,
        use_checkpoint: bool = True,
    ):
        super().__init__()
        self.client = TouristInfoClient(
            session=self.session,
            settings=self.settings,
            api_caller=self._safe_request,
        )
        self.fetch_details = fetch_details
        self.max_detail_count = max_detail_count
        self.use_checkpoint = use_checkpoint
        
        if self.use_checkpoint:
            self.checkpoint = TouristCheckpoint(self.settings.cache_dir)
        else:
            self.checkpoint = None

    def collect(self, province: Optional[str] = None) -> list[CertRecord]:
        """데이터 수집 (BaseAdapter 시그니처 준수)"""
        if not self.settings.data_go_kr_key:
            logger.warning('tourist_info_skip_no_api_key')
            return []

        lDongRegnCd = self.client.LDONG_REGN_CODE_MAP.get(province or '')
        if not lDongRegnCd:
            logger.warning('tourist_info_no_ldong_code', province=province)
            return []

        existing_ids = self.checkpoint.get_existing_ids() if self.checkpoint else set()
        logger.info(
            'tourist_info_collect_start',
            province=province, lDongRegnCd=lDongRegnCd,
            existing_count=len(existing_ids),
        )

        # 1. 목록 조회 (페이징)
        all_items = []
        new_items = []
        page = 1

        while page <= 10:
            items, total = self.client.fetch_list_page(lDongRegnCd, page, self.PAGE_SIZE)
            if not items:
                break
                
            all_items.extend(items)
            for item in items:
                content_id = int(item.get('contentid', 0))
                if content_id not in existing_ids:
                    new_items.append(item)
                    
            if len(all_items) >= total:
                break
            page += 1
            time.sleep(0.3)

        if not new_items:
            logger.info('tourist_info_no_new_items', total_fetched=len(all_items))
            return []

        # 2. 상세 조회 (쿼터 방어)
        detail_items = new_items[:self.max_detail_count] if self.fetch_details else []
        details_cache = {}

        for item in detail_items:
            content_id = int(item.get('contentid', 0))
            details_cache[content_id] = {
                'detail': self.client.fetch_detail_common(content_id),
                'intro': self.client.fetch_detail_intro(content_id),
            }
            time.sleep(self.DETAIL_DELAY)

        # 3. 도메인 매핑
        records = []
        new_content_ids = []
        
        for item in new_items:
            content_id = int(item.get('contentid', 0))
            detail_info = details_cache.get(content_id, {'detail': {}, 'intro': {}})
            
            record = map_to_record(
                item=item,
                detail=detail_info['detail'],
                intro=detail_info['intro'],
                source=self.source,
                cert_type=self.cert_type,
            )
            
            if record:
                records.append(record)
                new_content_ids.append(content_id)
                self._register_raw_row({
                    'content_id': content_id,
                    'title': record.raw_name,
                    'addr': record.raw_address,
                    'main_menu': record.main_menu,
                    'menus': record.menus,
                })

        # 4. 체크포인트 업데이트
        if self.checkpoint and new_content_ids:
            self.checkpoint.update(new_content_ids)

        logger.info(
            'tourist_info_collect_complete',
            count=len(records),
            new_count=len(new_items),
            with_details=len(details_cache),
        )
        return records