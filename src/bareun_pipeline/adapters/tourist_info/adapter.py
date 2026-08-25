"""
Bareun Pipeline: Tourist Info Adapter (Orchestrator)
=====================================================
- 기존 660줄의 God Object를 대체하는 최종 진입점
- Client(HTTP) + Mapper(변환) + Checkpoint(상태) 조율
- 🆕 QuotaManager 통합 + 체크포인트 리플레이 폴백
- 🆕 Type Hardening (dict[Unknown] → dict[str, Any] 명시)
- 🆕 detailCommon2 제거 (빈 응답으로 인한 무한 대기 방지)

위치: src/bareun_pipeline/adapters/tourist_info/adapter.py
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from ...bronze.tourist_content_tracker import TouristCheckpoint
from ...domain.record import CertRecord
from ...observability.logging import get_logger
from ...observability.quota import QuotaExceededError, QuotaManager
from ..base import BaseAdapter
from .client import TouristInfoClient
from .mapper import map_to_record

logger = get_logger(__name__)


class TouristInfoAdapter(BaseAdapter):
    """국문 관광정보 서비스 어댑터 (음식점 특화)"""

    source = 'tourist_info'
    cert_type = 'tourist_info'
    quota_provider = 'tourist_info'
    pii_fields: set[str] = set()

    PAGE_SIZE = 100
    DETAIL_DELAY = 0.5

    def __init__(
        self,
        fetch_details: bool = True,
        max_detail_count: int = 100,
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
        self._quota = QuotaManager.get_instance()

        if self.use_checkpoint:
            self.checkpoint = TouristCheckpoint(self.settings.cache_dir)
        else:
            self.checkpoint = None

    def collect(self, province: str | None = None) -> list[CertRecord]:
        """
        데이터 수집 (쿼터 사전 확인 + 체크포인트 리플레이 폴백)
        """
        if not self.settings.data_go_kr_key:
            logger.warning('tourist_info_skip_no_api_key')
            return []

        remaining = self._quota.get_remaining('tourist_info')
        if remaining < 10:
            logger.warning(
                'tourist_info_quota_low_trying_replay',
                remaining=remaining,
            )
            return self._replay_from_checkpoint(province)

        lDongRegnCd = self.client.LDONG_REGN_CODE_MAP.get(province or '')
        if not lDongRegnCd:
            logger.warning('tourist_info_no_ldong_code', province=province)
            return []

        existing_ids: set[int] = self.checkpoint.get_existing_ids() if self.checkpoint else set()
        logger.info(
            'tourist_info_collect_start',
            province=province,
            lDongRegnCd=lDongRegnCd,
            existing_count=len(existing_ids),
            quota_remaining=remaining,
        )

        all_items: list[dict[str, Any]] = []
        new_items: list[dict[str, Any]] = []
        page = 1

        try:
            while page <= 10:
                items, total = self.client.fetch_list_page(
                    lDongRegnCd,
                    page,
                    self.PAGE_SIZE,
                )

                if not items and page == 1:
                    logger.warning(
                        'tourist_info_api_blocked_trying_replay',
                        reason='403 SERVICE_KEY_IS_NOT_REGISTERED 또는 쿼터 초과',
                    )
                    return self._replay_from_checkpoint(province)

                if not items:
                    break

                all_items.extend(items)
                item: dict[str, Any]
                for item in items:
                    content_id = int(item.get('contentid', 0))
                    if content_id not in existing_ids:
                        new_items.append(item)

                if len(all_items) >= total:
                    break
                page += 1
                time.sleep(0.3)

        except QuotaExceededError as e:
            logger.warning(
                'tourist_info_quota_exceeded_during_fetch',
                error=str(e),
            )
            return self._replay_from_checkpoint(province)

        if not new_items:
            logger.info('tourist_info_no_new_items', total_fetched=len(all_items))
            return self._replay_from_checkpoint(province)

        detail_items: list[dict[str, Any]] = (
            new_items[: self.max_detail_count] if self.fetch_details else []
        )
        details_cache: dict[int, dict[str, Any]] = {}

        # 🆕 required_quota 수정: detailCommon2 제거로 2배 → 1배
        required_quota = len(detail_items)  # 🆕 * 2 제거
        if self._quota.get_remaining('tourist_info') < required_quota:
            logger.warning(
                'tourist_info_skip_details_quota_insufficient',
                required=required_quota,
                remaining=self._quota.get_remaining('tourist_info'),
            )
            detail_items = []

        # 🆕 상세 조회 진행 상황 추적용 카운터
        detail_total = len(detail_items)
        detail_success_intro = 0
        detail_success_common = 0  # 항상 0 (detailCommon2 호출 안 함)

        logger.info(
            'tourist_info_detail_fetch_start',
            total=detail_total,
            estimated_seconds=detail_total,  # 🆕 * 2 제거
        )

        item: dict[str, Any]
        for idx, item in enumerate(detail_items, start=1):
            content_id = int(item.get('contentid', 0))

            try:
                # 🆕 detailIntro2만 호출 (메뉴/영업시간이 핵심 가치)
                try:
                    intro_result = self.client.fetch_detail_intro(content_id)
                    if intro_result:
                        detail_success_intro += 1
                except Exception as e:
                    logger.warning(
                        'tourist_info_detail_intro_failed',
                        content_id=content_id,
                        error=str(e),
                        error_type=type(e).__name__,
                    )
                    intro_result = {}

                # 🆕 detailCommon2 호출 제거 (계속 빈 응답 반환)
                detail_result = {}

                details_cache[content_id] = {
                    'detail': detail_result,
                    'intro': intro_result,
                }

                if idx % 10 == 0 or idx == detail_total:
                    logger.info(
                        'tourist_info_detail_progress',
                        processed=f'{idx}/{detail_total}',
                        intro_success=detail_success_intro,
                        common_success=detail_success_common,
                    )

                # 🆕 time.sleep: 1.0초 → 0.5초 (성능 2배)
                time.sleep(0.5)

            except QuotaExceededError:
                logger.warning(
                    'tourist_info_quota_exceeded_during_details',
                    fetched=len(details_cache),
                )
                break
            except Exception as e:
                logger.error(
                    'tourist_info_detail_unexpected_error',
                    content_id=content_id,
                    error=str(e),
                    error_type=type(e).__name__,
                )
                continue

        logger.info(
            'tourist_info_detail_fetch_complete',
            total=detail_total,
            intro_success=detail_success_intro,
            common_success=detail_success_common,
        )

        records: list[CertRecord] = []
        new_content_ids: list[int] = []
        fetched_at = datetime.now(UTC).isoformat()

        item: dict[str, Any]
        for item in new_items:
            content_id = int(item.get('contentid', 0))
            detail_info: dict[str, Any] = details_cache.get(
                content_id,
                {'detail': {}, 'intro': {}},
            )

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

                self._register_raw_row(
                    {
                        'content_id': content_id,
                        'title': record.raw_name,
                        'addr': record.raw_address,
                        'main_menu': record.main_menu,
                        'menus': record.menus,
                        'province': province,
                        'municipality': record.municipality,
                        'emd': record.emd,
                        'lat': record.lat,
                        'lng': record.lng,
                        'fetched_at': fetched_at,
                    }
                )

        if self.checkpoint and new_content_ids:
            self.checkpoint.update(new_content_ids)

        logger.info(
            'tourist_info_collect_complete',
            count=len(records),
            new_count=len(new_items),
            with_details=len(details_cache),
            quota_remaining=self._quota.get_remaining('tourist_info'),
        )
        return records

    def _replay_from_checkpoint(self, province: str | None) -> list[CertRecord]:
        """체크포인트 기반 리플레이 (API 차단/쿼터 초과 시 우회)"""
        if not self.checkpoint:
            logger.warning('tourist_info_replay_skip_no_checkpoint')
            return []

        try:
            from ...bronze.raw_archiver import RawArchiver

            archiver = RawArchiver()
            rows = archiver.load_latest('tourist_info', province or 'nationwide')

            if not rows:
                logger.warning('tourist_info_replay_no_local_data')
                return []

            existing_ids = self.checkpoint.get_existing_ids()
            valid_rows: list[dict[str, Any]] = [
                row for row in rows if int(row.get('content_id', 0)) in existing_ids
            ]

            if not valid_rows:
                logger.warning(
                    'tourist_info_replay_no_matching_ids',
                    checkpoint_count=len(existing_ids),
                    raw_count=len(rows),
                )
                return []

            records: list[CertRecord] = []
            row: dict[str, Any]
            for row in valid_rows:
                record = CertRecord(
                    source=self.source,
                    cert_type=self.cert_type,
                    raw_name=row.get('title', ''),
                    raw_address=row.get('addr', ''),
                    main_menu=row.get('main_menu'),
                    menus=row.get('menus', []),
                    province=row.get('province'),
                    municipality=row.get('municipality'),
                    emd=row.get('emd'),
                    lat=row.get('lat'),
                    lng=row.get('lng'),
                    raw={
                        'content_id': row.get('content_id'),
                        'item': row.get('item', {}),
                        'detail': row.get('detail', {}),
                        'intro': row.get('intro', {}),
                    },
                )

                if record.is_valid():
                    records.append(record)

            logger.info(
                'tourist_info_replay_success',
                count=len(records),
                checkpoint_count=len(existing_ids),
                raw_count=len(valid_rows),
            )
            return records

        except Exception as e:
            logger.error(
                'tourist_info_replay_failed',
                error=str(e),
                error_type=type(e).__name__,
            )
            return []
