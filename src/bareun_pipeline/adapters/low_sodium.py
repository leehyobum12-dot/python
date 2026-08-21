"""
식약처 나트륨 줄이기 실천음식점 어댑터
========================================
- 공공데이터포털 REST API (JSON)
- 병렬 페이지 수집 (ThreadPoolExecutor)
- SITE_ADDR로 시도 필터링

기존 참고:
- adapters/low_sodium.py 전체 구조

개선 사항:
- BaseAdapter 상속 (Circuit Breaker, 타임아웃 자동 적용)
- 구조화 로깅
- PII 필드 명시적 정의
- JSON 파싱 에러 핸들링 강화
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional, Tuple
from urllib.parse import unquote

from .base import BaseAdapter, parse_addr_tokens
from ..domain.record import CertRecord
from ..observability.logging import get_logger


logger = get_logger(__name__)


class LowSodiumAdapter(BaseAdapter):
    """
    식약처 나트륨 줄이기 실천음식점 어댑터

    API 정보:
    - URL: https://apis.data.go.kr/1471000/NaReduceRstrtDsgnBsshRgstr/getNaReduceRstrtDsgnBsshRgstr
    - 형식: JSON
    - 파라미터: serviceKey, pageNo, numOfRows, type

    PII 필드:
    - RPRS_NM: 대표자명
    """

    # 어댑터 식별자
    source = 'low_sodium'
    cert_type = 'low_sodium'
    quota_provider = None  # 공공 API이므로 Quota 미적용

    # PII 필드 정의 (raw_archiver에서 마스킹용)
    pii_fields = {
        'RPRS_NM',  # 대표자명
    }

    # API 설정
    BASE_URL = 'https://apis.data.go.kr/1471000/NaReduceRstrtDsgnBsshRgstr'
    OPERATION = '/getNaReduceRstrtDsgnBsshRgstr'
    MAX_WORKERS = 6
    PAGE_SIZE = 500

    def __init__(self):
        super().__init__()
        self.service_key = self.settings.data_go_kr_key or ''

    def _key(self) -> str:
        """URL 인코딩된 키 처리"""
        return unquote(self.service_key) if '%' in self.service_key else self.service_key

    def _fetch_page(self, page: int, province_prefixes: Tuple[str, ...]):
        """단일 페이지 수집"""
        if not self.service_key:
            return [], 0, []

        url = f'{self.BASE_URL}{self.OPERATION}'
        params = {
            'serviceKey': self._key(),
            'pageNo': page,
            'numOfRows': self.PAGE_SIZE,
            'type': 'json',
        }

        response = self._safe_request('GET', url, params=params)

        if response is None or response.status_code != 200:
            logger.error(
                'low_sodium_fetch_failed',
                page=page,
                status_code=response.status_code if response else None,
            )
            return [], 0, []

        try:
            data = response.json()
            body = data.get('body', {})
            items = body.get('items', [])

            # items 정규화 (패턴 A, B, C 처리)
            if isinstance(items, dict):
                items = items.get('item', [])
                if not isinstance(items, list):
                    items = [items] if items else []
            elif isinstance(items, list) and items:
                first = items[0]
                if isinstance(first, dict) and 'item' in first and len(first.keys()) == 1:
                    unwrapped = []
                    for item_wrapper in items:
                        if isinstance(item_wrapper, dict) and 'item' in item_wrapper:
                            unwrapped.append(item_wrapper['item'])
                    items = unwrapped

            if not isinstance(items, list):
                items = [items] if items else []

            total = body.get('totalCount', 0)

            records: List[CertRecord] = []
            raw_rows: List[dict] = []

            for item in items:
                if not isinstance(item, dict):
                    continue

                raw_name = (item.get('BSSH_NM') or '').strip()
                raw_address = (item.get('SITE_ADDR') or '').strip()

                if not raw_name or not raw_address:
                    continue

                p, m, e = parse_addr_tokens(raw_address)
                raw_rows.append(item)

                # 주소 필터링
                if province_prefixes:
                    matched = False
                    
                    if p and p in province_prefixes:
                        matched = True
                    
                    if not matched:
                        for prefix in province_prefixes:
                            if raw_address.startswith(prefix):
                                matched = True
                                break
                            province_short = prefix.replace('특별시', '').replace('광역시', '').replace('특별자치도', '').replace('특별자치시', '')
                            if raw_address.startswith(province_short):
                                matched = True
                                break
                    
                    if not matched:
                        continue

                record = CertRecord(
                    source=self.source,
                    cert_type=self.cert_type,
                    raw_name=raw_name,
                    raw_address=raw_address,
                    province=p,
                    municipality=m,
                    emd=e,
                    biz_type=(item.get('INDUTY_CD_NM') or '').strip(),
                    designated_at=(item.get('JOIN_YMD') or item.get('APPT_YMD') or '').strip(),
                    is_active=True,
                    raw=item,
                )
                records.append(record)

            return records, total, raw_rows

        except Exception as e:
            logger.error(
                'low_sodium_page_error',
                page=page,
                error=str(e),
                error_type=type(e).__name__,
            )
            return [], 0, []

    def collect(self, province: Optional[str] = None) -> List[CertRecord]:
        """데이터 수집"""
        if not self.service_key:
            logger.warning('low_sodium_skip_no_api_key')
            return []

        from .base import match_prefixes
        prefixes = match_prefixes(province) if province else ()

        logger.info('low_sodium_collect_start', province=province, prefixes=prefixes)

        # 첫 페이지로 전체 건수 파악
        first_records, total, first_raw = self._fetch_page(1, prefixes)

        logger.info(
            'low_sodium_first_page',
            records=len(first_records),
            total=total,
            prefixes=prefixes,
        )

        if total == 0:
            logger.warning('low_sodium_no_data', province=province)
            return []

        all_records = list(first_records)
        self.raw_rows.extend(first_raw)

        if total <= self.PAGE_SIZE:
            valid_records = [r for r in all_records if r.is_valid()]
            logger.info(
                'low_sodium_collect_complete',
                count=len(valid_records),
                total=total,
            )
            return valid_records

        # 나머지 페이지 병렬 수집
        pages = list(range(2, (total // self.PAGE_SIZE) + 2))
        logger.info(
            'low_sodium_parallel_fetch',
            pages=len(pages),
            total=total,
        )

        with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as executor:
            futures = {
                executor.submit(self._fetch_page, p, prefixes): p
                for p in pages
            }
            for future in as_completed(futures):
                page = futures[future]
                try:
                    page_records, _, page_raw = future.result()
                    all_records.extend(page_records)
                    self.raw_rows.extend(page_raw)
                except Exception as e:
                    logger.error(
                        'low_sodium_page_failed',
                        page=page,
                        error=str(e),
                    )

        valid_records = [r for r in all_records if r.is_valid()]
        
        # 제주도 데이터 확인 로깅
        jeju_records = [r for r in valid_records if r.province == '제주특별자치도']
        logger.info(
            'low_sodium_collect_complete',
            count=len(valid_records),
            total=total,
            jeju_count=len(jeju_records),
            sample_addresses=[r.raw_address[:30] for r in valid_records[:5]],
        )

        return valid_records