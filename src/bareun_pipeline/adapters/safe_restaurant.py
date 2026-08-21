"""
농식품부 안심식당 어댑터
=========================
- XML API 파싱
- 병렬 페이지 수집 (ThreadPoolExecutor)
- RELAX_USE_YN으로 활성 상태 판단

기존 참고:
- adapters/safe_restaurant.py 전체 구조

개선 사항:
- BaseAdapter 상속 (Circuit Breaker, 타임아웃 자동 적용)
- 구조화 로깅
- PII 필드 명시적 정의
- XML 파싱 에러 핸들링 강화
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional, Tuple

from .base import BaseAdapter, parse_addr_tokens
from ..domain.record import CertRecord
from ..observability.logging import get_logger


logger = get_logger(__name__)


class SafeRestaurantAdapter(BaseAdapter):
    """
    농식품부 안심식당 API 어댑터

    API 정보:
    - URL: http://211.237.50.150:7080/openapi/{key}/xml/{service_id}/{start}/{end}
    - 형식: XML
    - 파라미터: RELAX_SI_NM (시도명, 선택)

    PII 필드:
    - RELAX_RSTRNT_REPRESENT: 대표자 실명
    """

    # 어댑터 식별자
    source = 'safe_restaurant'
    cert_type = 'safe_restaurant'
    quota_provider = None  # 공공 API이므로 Quota 미적용

    # PII 필드 정의 (raw_archiver에서 마스킹용)
    pii_fields = {
        'RELAX_RSTRNT_REPRESENT',  # 대표자 실명
    }

    # API 설정
    BASE_URL = 'http://211.237.50.150:7080/openapi'
    SERVICE_ID = 'Grid_20200713000000000605_1'
    PAGE_SIZE = 1000
    MAX_WORKERS = 8

    def __init__(self):
        super().__init__()
        self.api_key = self.settings.mafra_api_key or ''

    def _fetch_page(
        self,
        start: int,
        end: int,
        province: Optional[str],
    ) -> Tuple[List[CertRecord], int, List[dict]]:
        """
        단일 페이지 수집

        Args:
            start: 시작 인덱스
            end: 종료 인덱스
            province: 시도명 (None이면 전국)

        Returns:
            (레코드 목록, 전체 건수, 원본 데이터 목록)
        """
        if not self.api_key:
            logger.warning('safe_restaurant_no_api_key')
            return [], 0, []

        url = f'{self.BASE_URL}/{self.api_key}/xml/{self.SERVICE_ID}/{start}/{end}'
        params = {'RELAX_SI_NM': province} if province else {}

        response = self._safe_request('GET', url, params=params)

        if response is None or response.status_code != 200:
            logger.error(
                'safe_restaurant_fetch_failed',
                start=start,
                end=end,
                status_code=response.status_code if response else None,
            )
            return [], 0, []

        try:
            root = ET.fromstring(response.content)
            rows = root.findall('.//row')
            total = int(root.findtext('totalCnt') or 0)

            page_records: List[CertRecord] = []
            page_raw_rows: List[dict] = []

            for row in rows:
                raw = {c.tag: (c.text or '').strip() for c in row}
                use_yn = (row.findtext('RELAX_USE_YN') or '').strip()
                cancel_dt = (row.findtext('RELAX_RSTRNT_CNCL_DT') or '').strip()

                page_raw_rows.append(raw)

                record = CertRecord(
                    source=self.source,
                    cert_type=self.cert_type,
                    raw_name=(row.findtext('RELAX_RSTRNT_NM') or '').strip(),
                    raw_address=(row.findtext('RELAX_ADD1') or '').strip(),
                    province=(row.findtext('RELAX_SI_NM') or '').strip(),
                    municipality=(row.findtext('RELAX_SIDO_NM') or '').strip(),
                    phone=(row.findtext('RELAX_RSTRNT_TEL') or '').strip(),
                    biz_type=(row.findtext('RELAX_GUBUN_DETAIL') or '').strip(),
                    designated_at=(row.findtext('RELAX_RSTRNT_REG_DT') or '').strip(),
                    cancelled_at=cancel_dt or None,
                    is_active=(use_yn == 'Y' and not cancel_dt),
                    raw=raw,
                )
                page_records.append(record)

            logger.debug(
                'safe_restaurant_page_fetched',
                start=start,
                end=end,
                records=len(page_records),
                total=total,
            )

            return page_records, total, page_raw_rows

        except ET.ParseError as e:
            logger.error(
                'safe_restaurant_xml_parse_error',
                start=start,
                end=end,
                error=str(e),
            )
            return [], 0, []

        except Exception as e:
            logger.error(
                'safe_restaurant_unknown_error',
                start=start,
                end=end,
                error=str(e),
                error_type=type(e).__name__,
            )
            return [], 0, []

    def collect(self, province: Optional[str] = None) -> List[CertRecord]:
        """
        데이터 수집

        Args:
            province: 시도명 (None이면 전국)

        Returns:
            수집된 CertRecord 목록 (활성 상태만)
        """
        if not self.api_key:
            logger.warning('safe_restaurant_skip_no_api_key')
            return []

        logger.info('safe_restaurant_collect_start', province=province)

        # 첫 페이지로 전체 건수 파악
        first_records, total, first_raw = self._fetch_page(1, self.PAGE_SIZE, province)

        if not first_records:
            logger.warning('safe_restaurant_no_data', province=province)
            return []

        all_records = list(first_records)
        self.raw_rows.extend(first_raw)

        if total <= self.PAGE_SIZE:
            valid_records = [r for r in all_records if r.is_valid()]
            logger.info(
                'safe_restaurant_collect_complete',
                count=len(valid_records),
                total=total,
            )
            return valid_records

        # 나머지 페이지 병렬 수집
        ranges: List[Tuple[int, int]] = []
        start = self.PAGE_SIZE + 1
        while start <= total:
            end = min(start + self.PAGE_SIZE - 1, total)
            ranges.append((start, end))
            start = end + 1

        logger.info(
            'safe_restaurant_parallel_fetch',
            pages=len(ranges),
            total=total,
        )

        with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as executor:
            futures = {
                executor.submit(self._fetch_page, s, e, province): (s, e)
                for s, e in ranges
            }
            for future in as_completed(futures):
                start_idx, end_idx = futures[future]
                try:
                    page_records, _, page_raw = future.result()
                    all_records.extend(page_records)
                    self.raw_rows.extend(page_raw)
                except Exception as e:
                    logger.error(
                        'safe_restaurant_page_failed',
                        start=start_idx,
                        end=end_idx,
                        error=str(e),
                    )

        valid_records = [r for r in all_records if r.is_valid()]
        logger.info(
            'safe_restaurant_collect_complete',
            count=len(valid_records),
            total=total,
        )

        return valid_records