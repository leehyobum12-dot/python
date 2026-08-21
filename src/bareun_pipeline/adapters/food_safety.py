"""
식품안심업소(위생등급) 어댑터
===============================
- 멀티 벤더 페일오버 (data.go.kr → 식품안전나라 → 로컬 리플레이)
- 병렬 페이지 수집 (ThreadPoolExecutor)
- 지수 백오프 재시도 (INFO-500 대응)
- FIELD_CANDIDATES로 유연한 필드 매핑

보존 사항:
- 멀티 벤더 페일오버 (API 장애 시 데이터 수집 지속성)
- FIELD_CANDIDATES (스키마 변경 대응)

개선 사항:
- 병렬 페이지 수집 (19분 → 3~4분)
- 지수 백오프 재시도 (INFO-500 대응)
- 청크 크기 축소 (500 → 300, 서버 부하 감소)
- 재시도 중복 제거 (벤더 레벨 vs 페이지 레벨)
"""
from __future__ import annotations

import json
import os
import time
from typing import List, Optional, Dict, Any, Tuple
from urllib.parse import unquote
from concurrent.futures import ThreadPoolExecutor, as_completed
from .base import BaseAdapter, parse_addr_tokens, match_prefixes
from ..domain.record import CertRecord
from ..observability.logging import get_logger


logger = get_logger(__name__)


class FoodSafetyAdapter(BaseAdapter):
    """
    식품안심업소(위생등급) 어댑터

    멀티 벤더 페일오버 전략:
    1순위: data.go.kr 미러 (FOOD_SAFETY_DATAGO_URL + DATA_GO_KR_KEY)
    2순위: 식약처 원본 C004 (FOODSAFETY_KOREA_KEY)
    3순위: 로컬 원본 리플레이

    API 정보:
    - data.go.kr: https://apis.data.go.kr/1471000/{service}/{operation}
    - 식품안전나라: http://openapi.foodsafetykorea.go.kr/api/{key}/C004/json/{start}/{end}
    - 형식: JSON

    PII 필드:
    - PRSDNT_NM: 대표자명
    - WRKR_REG_NO: 종사자 등록번호
    """

    # 어댑터 식별자
    source = 'food_safety'
    cert_type = 'food_safety'
    quota_provider = None

    # PII 필드 정의
    pii_fields = {
        'PRSDNT_NM',
        'WRKR_REG_NO',
    }

    # API 설정
    DATAGO_ORG = '1471000'
    CANDIDATES = [
        ('FoodSafetyRstrn', 'getFoodSafetyRstrn'),
        ('FoodSafetyRestaurant', 'getFoodSafetyRestaurant'),
        ('HsncyStdrRstrn', 'getHsncyStdrRstrn'),
        ('FoodSafetyGradeRstrn', 'getFoodSafetyGradeRstrn'),
    ]
    MFDS_URL_TPL = 'http://openapi.foodsafetykorea.go.kr/api/{key}/C004/json/{start}/{end}'

    # 수집 설정
    PAGE_SIZE = 300              # 청크 크기 (서버 부하 감소)
    TIMEOUT = 15                 # 요청 타임아웃 (빠른 실패 감지)
    REPLAY_MAX_AGE_DAYS = 7

    # 병렬 수집 설정
    PARALLEL_PAGE_WORKERS = 6    # 병렬 워커 수
    PAGE_MAX_RETRIES = 3         # 페이지별 최대 재시도
    PAGE_RETRY_DELAY = 1.0       # 재시도 기본 대기 (초)
    MAX_TOTAL_PAGES = 200        # 최대 페이지 상한 (무한 수집 방지)

    # 필드 후보 (스키마 변경 대응)
    FIELD_CANDIDATES: Dict[str, tuple] = {
        'name': ('BSSH_NM', 'UPSO_NM', 'ENTRPS_NM', '업소명', '상호명', '영업소명'),
        'addr': ('ADDR', 'SITE_ADDR', 'RDNMADR', 'ROAD_NM_ADDR', '도로명주소', '소재지도로명주소', '영업장소재지', '소재지'),
        'lot': ('JIBUN_ADDR', 'LOT_ADDR', '지번주소'),
        'phone': ('TELNO', 'TEL', '전화번호', '업소전화번호'),
        'biz': ('INDUTY_NM', 'INDUTY_CD_NM', '업종명', '업태구분명'),
        'designated': ('ASGN_FROM', 'ASGN_YMD', 'PRMISN_YMD', '지정일자', '허가일자', '지정시작일자'),
        'status': ('CLSBIZ_DVS_CD_NM', '영업상태', 'BIZ_STT_CD_NM'),
        'closed': ('CLSBIZ_DT', '폐업일자', 'CLOSE_DT'),
        'cancel': ('ASGN_CANCEL_YMD', '지정취소일자'),
        'menu': ('MAIN_MENU', 'MENU_NM', '대표메뉴', '주메뉴'),
    }

    def __init__(self, snapshot_first: bool = False):
        super().__init__()
        self.service_key = self.settings.foodsafety_korea_key or ''
        self.datago_key = self.settings.data_go_kr_key or ''
        self.snapshot_first = snapshot_first

    def _key(self) -> str:
        """URL 인코딩된 키 처리"""
        return unquote(self.service_key) if '%' in self.service_key else self.service_key

    def _datago_key(self) -> str:
        """data.go.kr 키 처리"""
        return unquote(self.datago_key) if '%' in self.datago_key else self.datago_key

    def _get_any(self, d: dict, field: str) -> str:
        """필드 후보에서 값 추출"""
        if not isinstance(d, dict):
            return ''
        for k in self.FIELD_CANDIDATES.get(field, ()):
            v = d.get(k)
            if v:
                return str(v).strip()
        return ''

    def _normalize_items(self, items: Any) -> list:
        """items 정규화"""
        if items in (None, '', [], {}):
            return []
        if isinstance(items, dict):
            items = items.get('item', [])
        if items in (None, ''):
            return []
        if isinstance(items, dict):
            return [items]
        return items if isinstance(items, list) else []

    def _parse_rows(
        self,
        rows: list,
        province: Optional[str],
    ) -> List[CertRecord]:
        """행 파싱"""
        records: List[CertRecord] = []
        prefixes = match_prefixes(province) if province else None

        for item in rows:
            addr = self._get_any(item, 'addr')
            if not addr:
                continue

            if prefixes and not addr.startswith(prefixes):
                continue

            p, m, e = parse_addr_tokens(addr)
            clsbiz_dt = self._get_any(item, 'closed')
            cancel_dt = self._get_any(item, 'cancel')
            status = self._get_any(item, 'status')

            record = CertRecord(
                source=self.source,
                cert_type=self.cert_type,
                raw_name=self._get_any(item, 'name'),
                raw_address=addr,
                province=p,
                municipality=m,
                emd=e,
                road_name=addr,
                lot_num=self._get_any(item, 'lot') or '',
                main_menu=self._get_any(item, 'menu'),
                phone=self._get_any(item, 'phone'),
                biz_type=self._get_any(item, 'biz'),
                designated_at=self._get_any(item, 'designated'),
                cancelled_at=cancel_dt or None,
                closed_at=clsbiz_dt or None,
                is_active=('폐업' not in status and not clsbiz_dt and not cancel_dt),
                raw=item,
            )
            records.append(record)

        return records

    def _get_strategies(self) -> List[tuple]:
        """수집 전략 목록 반환"""
        strategies = []

        env_url = os.getenv('FOOD_SAFETY_DATAGO_URL', '').strip()
        if self.datago_key and env_url:
            strategies.append(('datago', env_url))

        if self.service_key:
            strategies.append(('mfds', self.MFDS_URL_TPL))

        return strategies

    # ═══════════════════════════════════════════════════════════
    # 메인 수집 로직
    # ═══════════════════════════════════════════════════════════

    def collect(self, province: Optional[str] = None) -> List[CertRecord]:
        """
        데이터 수집

        전략:
        1. 스냅샷 모드이면 로컬 리플레이
        2. datago + mfds 병렬 시도
        3. 먼저 성공한 벤더 결과 반환
        4. 모두 실패 시 로컬 리플레이
        """
        if not self.service_key and not self.datago_key:
            logger.warning('food_safety_skip_no_api_key')
            return []

        logger.info('food_safety_collect_start', province=province)

        # 스냅샷 모드
        if self.snapshot_first and province:
            records = self._replay_from_raw(province)
            if records:
                logger.info(
                    'food_safety_snapshot_loaded',
                    count=len(records),
                    province=province,
                )
                return [r for r in records if r.is_valid()]

        # 벤더별 병렬 시도
        strategies = self._get_strategies()
        logger.info(
            'food_safety_vendor_parallel_start',
            strategies=[s[0] for s in strategies],
        )

        with ThreadPoolExecutor(max_workers=len(strategies)) as executor:
            futures = {}
            for kind, url in strategies:
                future = executor.submit(self._fetch_from_vendor, kind, url, province)
                futures[future] = kind

            for future in as_completed(futures):
                vendor_kind = futures[future]
                try:
                    collected_rows = future.result()

                    if collected_rows:
                        self.raw_rows = collected_rows
                        records = self._parse_rows(collected_rows, province)

                        logger.info(
                            'food_safety_vendor_success',
                            vendor=vendor_kind,
                            count=len(records),
                            raw_count=len(collected_rows),
                        )
                        return [r for r in records if r.is_valid()]
                    else:
                        logger.warning(
                            'food_safety_vendor_empty',
                            vendor=vendor_kind,
                        )

                except Exception as e:
                    logger.error(
                        'food_safety_vendor_failed',
                        vendor=vendor_kind,
                        error=str(e),
                        error_type=type(e).__name__,
                    )

        # 모든 벤더 실패 → 로컬 리플레이
        logger.warning('food_safety_all_vendors_failed_trying_replay')
        records = self._replay_from_raw(province)
        if records:
            logger.info('food_safety_replay_success', count=len(records))
            return [r for r in records if r.is_valid()]

        logger.error('food_safety_all_failed')
        return []

    def _fetch_from_vendor(
        self,
        kind: str,
        url: str,
        province: Optional[str],
    ) -> list:
        """벤더에서 데이터 수집"""
        if kind == 'datago':
            return self._fetch_datago(url)
        elif kind == 'mfds':
            return self._fetch_mfds()
        return []

    # ═══════════════════════════════════════════════════════════
    # data.go.kr 수집
    # ═══════════════════════════════════════════════════════════

    def _fetch_datago(self, url: str) -> list:
        """data.go.kr에서 수집"""
        all_items = []
        page = 1

        while True:
            params = {
                'serviceKey': self._datago_key(),
                'pageNo': page,
                'numOfRows': self.PAGE_SIZE,
                'type': 'json',
            }

            response = self._safe_request('GET', url, params=params)
            if response is None or response.status_code != 200:
                break

            try:
                data = response.json()
                body = (data.get('response') or {}).get('body') or {}
                items = self._normalize_items(body.get('items'))

                if not items:
                    break

                all_items.extend(items)

                if len(items) < self.PAGE_SIZE:
                    break

                page += 1
                time.sleep(0.4)

            except Exception as e:
                logger.error(
                    'food_safety_datago_parse_error',
                    page=page,
                    error=str(e),
                )
                break

        return all_items

    # ═══════════════════════════════════════════════════════════
    # 식품안전나라 (MFDS) 수집 — 병렬 + 지수 백오프
    # ═══════════════════════════════════════════════════════════

    def _fetch_mfds_page_with_retry(
        self,
        page: int,
        page_size: int,
    ) -> Optional[Tuple[list, int]]:
        """단일 페이지 수집 (지수 백오프 재시도)"""
        for attempt in range(1, self.PAGE_MAX_RETRIES + 1):
            result = self._fetch_mfds_page(page, page_size)

            if result is not None:
                return result

            if attempt < self.PAGE_MAX_RETRIES:
                wait_time = self.PAGE_RETRY_DELAY * (2 ** (attempt - 1))
                # DEBUG 레벨로 변경 (INFO → DEBUG)
                logger.debug(
                    'food_safety_mfds_page_retry',
                    page=page,
                    attempt=attempt,
                    wait_seconds=wait_time,
                )
                time.sleep(wait_time)

        # 실패는 WARNING 레벨 유지
        logger.warning(
            'food_safety_mfds_page_failed',
            page=page,
            max_retries=self.PAGE_MAX_RETRIES,
        )
        return None

    def _fetch_mfds_page(
        self,
        page: int,
        page_size: int,
    ) -> Optional[Tuple[list, int]]:
        """단일 페이지 수집 (재시도 없음)"""
        start = (page - 1) * page_size + 1
        end = page * page_size
        url = self.MFDS_URL_TPL.format(key=self._key(), start=start, end=end)

        response = self._safe_request('GET', url)
        if response is None or response.status_code != 200:
            logger.debug('food_safety_mfds_request_failed', page=page)
            return None

        try:
            response.encoding = 'utf-8'
            data = response.json()
            payload = data.get('C004', {})
            result = payload.get('RESULT')

            code = ''
            if isinstance(result, list) and result:
                code = (result[0] or {}).get('CODE', '')
            elif isinstance(result, dict):
                code = result.get('CODE', '')

            if code == 'ERROR-500':
                logger.error('food_safety_mfds_error_500', page=page)
                return None

            if code == 'INFO-500':
                # DEBUG 레벨로 변경
                logger.debug('food_safety_mfds_info_500', page=page)
                return None

            if code == 'INFO-200':
                logger.debug('food_safety_mfds_no_more_data', page=page)
                return [], 0

            if code not in (None, '', 'INFO-000'):
                logger.warning('food_safety_mfds_fatal_code', page=page, code=code)
                return None

            rows = payload.get('row', [])
            if isinstance(rows, dict):
                rows = [rows]

            if not rows:
                return [], 0

            total_count = 0
            if page == 1:
                total_count = payload.get('TOTAL_COUNT', 0)
                if not total_count and isinstance(result, list) and result:
                    total_count = (result[0] or {}).get('TOTAL_COUNT', 0)
                if isinstance(total_count, str):
                    try:
                        total_count = int(total_count)
                    except (ValueError, TypeError):
                        total_count = 0

            return rows, total_count

        except Exception as e:
            logger.error('food_safety_mfds_parse_error', page=page, error=str(e))
            return None

    def _fetch_mfds(self) -> list:
        """식품안전나라에서 수집 (병렬 + 지수 백오프 + 조기 종료)"""
        start_time = time.time()

        first_result = self._fetch_mfds_page_with_retry(1, self.PAGE_SIZE)
        if not first_result:
            logger.warning('food_safety_mfds_first_page_failed')
            return []

        first_items, total_count = first_result

        if not first_items:
            logger.info('food_safety_mfds_empty')
            return []

        if total_count > 0:
            total_pages = min((total_count // self.PAGE_SIZE) + 1, self.MAX_TOTAL_PAGES)
        else:
            total_pages = self.MAX_TOTAL_PAGES

        logger.info(
            'food_safety_mfds_start',
            total=total_count,
            pages=total_pages,
        )

        if total_pages <= 1:
            return first_items

        all_items = list(first_items)
        pages_to_fetch = list(range(2, total_pages + 1))

        success_pages = 0
        failed_pages = 0
        consecutive_failures = 0

        with ThreadPoolExecutor(max_workers=self.PARALLEL_PAGE_WORKERS) as executor:
            futures = {
                executor.submit(self._fetch_mfds_page_with_retry, page, self.PAGE_SIZE): page
                for page in pages_to_fetch
            }

            for future in as_completed(futures):
                page = futures[future]
                try:
                    result = future.result()
                    if result:
                        items, _ = result
                        if items:
                            all_items.extend(items)
                        success_pages += 1
                        consecutive_failures = 0
                    else:
                        failed_pages += 1
                        consecutive_failures += 1

                        if consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
                            logger.warning(
                                'food_safety_mfds_early_termination',
                                consecutive_failures=consecutive_failures,
                                remaining=total_pages - success_pages - failed_pages,
                            )
                            for f in futures:
                                if not f.done():
                                    f.cancel()
                            break

                except Exception as e:
                    failed_pages += 1
                    consecutive_failures += 1

        elapsed = time.time() - start_time
        logger.info(
            'food_safety_mfds_complete',
            items=len(all_items),
            success=success_pages + 1,
            failed=failed_pages,
            elapsed=round(elapsed, 1),
        )

        return all_items

    # ═══════════════════════════════════════════════════════════
    # 로컬 리플레이
    # ═══════════════════════════════════════════════════════════

    def _replay_from_raw(self, province: Optional[str]) -> List[CertRecord]:
        """로컬 원본 파일에서 리플레이"""
        from ..bronze.raw_archiver import RawArchiver

        archiver = RawArchiver()
        rows = archiver.load_latest(self.source, province or 'nationwide')

        if not rows:
            return []

        return self._parse_rows(rows, province)