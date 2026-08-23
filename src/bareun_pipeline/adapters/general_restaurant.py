"""
일반음식점 조회서비스 어댑터 (행정안전부)
==========================================
- 매일 업데이트되는 공식 영업신고 데이터
- 영업상태 (폐업/영업) 100% 정확
- 위생등급 (갑/을) 포함

실제 필드명 (영문):
- BPLC_NM: 사업장명
- ROAD_NM_ADDR: 도로명주소
- LOTNO_ADDR: 지번주소
- SALS_STTS_NM: 영업상태명
- GRD_SE_NM: 등급구분명
- CRD_INFO_X, CRD_INFO_Y: 좌표
- LCPMT_YMD: 인허가일자
- CLSBIZ_YMD: 폐업일자
- TELNO: 전화번호
- BZSTAT_SE_NM: 업태구분명
- OPN_ATMY_GRP_CD: 개방자치단체코드
"""
from __future__ import annotations

import time
from typing import List, Optional, Tuple
from urllib.parse import unquote
from concurrent.futures import ThreadPoolExecutor, as_completed

from .base import BaseAdapter, parse_addr_tokens
from ..domain.record import CertRecord
from ..observability.logging import get_logger


logger = get_logger(__name__)


class GeneralRestaurantAdapter(BaseAdapter):
    """일반음식점 조회서비스 어댑터 (Master Data Source)"""

    source = 'general_restaurant'
    cert_type = 'general_restaurant'
    quota_provider = 'general_restaurant'

    pii_fields = set()

    BASE_URL = 'https://apis.data.go.kr/1741000/general_restaurants'
    OPERATION = '/info'
    MAX_WORKERS = 4
    PAGE_SIZE = 500
    MAX_PAGES_DEFAULT = 100

    def __init__(self, max_pages: int = MAX_PAGES_DEFAULT):
        super().__init__()
        self.service_key = self.settings.data_go_kr_key or ''
        self.max_pages = max_pages

    def _key(self) -> str:
        return unquote(self.service_key) if '%' in self.service_key else self.service_key

    def _naver_to_wgs84(self, x: float, y: float) -> Tuple[Optional[float], Optional[float]]:
        """네이버 좌표계 (TM) → WGS84 변환"""
        try:
            from pyproj import Transformer
            transformer = Transformer.from_crs("EPSG:5179", "EPSG:4326", always_xy=True)
            lng, lat = transformer.transform(x, y)
            return lat, lng
        except ImportError:
            logger.warning('pyproj_not_installed_using_approximate')
            lat = (y - 500000) / 111320 + 33.0
            lng = (x - 950000) / (111320 * 0.8) + 126.0
            return lat, lng
        except Exception as e:
            logger.error('naver_to_wgs84_error', x=x, y=y, error=str(e))
            return None, None

    def _convert_coordinates(
        self,
        x: float,
        y: float,
    ) -> Tuple[Optional[float], Optional[float]]:
        """
        일반음식점 좌표 변환

        CRD_INFO_X / CRD_INFO_Y는 공공데이터별로 좌표계가 일관되지 않을 수 있으므로
        여러 한국 좌표계를 순차적으로 시도한다.

        반환:
            (lat, lng) 또는 (None, None)
        """
        # 이미 WGS84 경위도인 경우
        if 124.5 <= x <= 132.0 and 33.0 <= y <= 38.6:
            return y, x

        # 한국 전체 유효 범위
        KOREA_LAT_MIN = 33.0
        KOREA_LAT_MAX = 38.7
        KOREA_LNG_MIN = 124.5
        KOREA_LNG_MAX = 132.0

        try:
            from pyproj import Transformer
        except ImportError:
            logger.warning('pyproj_not_installed_coordinate_skipped')
            return None, None

        # 한국 공공데이터에서 자주 쓰이는 좌표계 후보
        candidates = [
            ('EPSG:5174', 'Korea 1985 Modified Central Belt'),
            ('EPSG:5179', 'Korea 2000 Unified CS'),
            ('EPSG:5181', 'Korea 2000 Central Belt'),
            ('EPSG:5185', 'Korea 2000 West Belt'),
            ('EPSG:5186', 'Korea 2000 Central Belt 2010'),
            ('EPSG:5187', 'Korea 2000 East Belt'),
            ('EPSG:5188', 'Korea 2000 East Sea Belt'),
            ('EPSG:2097', 'Korean 1985 Central Belt'),
            ('EPSG:2096', 'Korean 1985 East Belt'),
        ]

        for epsg, name in candidates:
            try:
                transformer = Transformer.from_crs(epsg, 'EPSG:4326', always_xy=True)
                lng, lat = transformer.transform(x, y)

                if (
                    KOREA_LAT_MIN <= lat <= KOREA_LAT_MAX
                    and KOREA_LNG_MIN <= lng <= KOREA_LNG_MAX
                ):
                    logger.debug(
                        'general_restaurant_coordinate_converted',
                        epsg=epsg,
                        crs_name=name,
                        x=x,
                        y=y,
                        lat=lat,
                        lng=lng,
                    )
                    return lat, lng

            except Exception:
                continue

        logger.warning(
            'general_restaurant_coordinate_convert_failed',
            x=x,
            y=y,
        )
        return None, None
    
    def _extract_hygiene_grade(self, grade: str) -> Optional[str]:
        """위생등급 추출 (갑/을/병)"""
        grade = (grade or '').strip()
        if grade in ('갑', '을', '병'):
            return f'hygiene_grade_{grade}'
        return None

    def _fetch_page(self, page: int, province_prefixes: tuple) -> Tuple[List[CertRecord], int, List[dict]]:
        """단일 페이지 수집 (영문 필드명 사용)"""
        if not self.service_key:
            return [], 0, []

        params = {
            'serviceKey': self._key(),
            'pageNo': page,
            'numOfRows': self.PAGE_SIZE,
            'type': 'json',
        }

        response = self._safe_request('GET', f'{self.BASE_URL}{self.OPERATION}', params=params)

        if response is None or response.status_code != 200:
            if response is not None:
                logger.error(
                    'general_restaurant_fetch_failed',
                    page=page,
                    status_code=response.status_code,
                    response_text=response.text[:300] if response.text else '',
                )
            return [], 0, []

        try:
            data = response.json()
            
            body = data.get('response', {}).get('body', {})
            items_container = body.get('items', [])
            total = body.get('totalCount', 0)

            if isinstance(items_container, dict):
                items = items_container.get('item', [])
            elif isinstance(items_container, list):
                items = items_container
            else:
                items = []

            if not isinstance(items, list):
                items = [items] if items else []

            # 디버깅: 첫 페이지에서 필드명 확인
            if page == 1 and items:
                all_keys = list(items[0].keys())
                logger.info(
                    'general_restaurant_field_check',
                    all_keys=all_keys,  # 🆕 전체 키 출력
                    sample_bplc_nm=items[0].get('BPLC_NM', '(없음)'),
                    sample_road_addr=items[0].get('ROAD_NM_ADDR', '(없음)'),
                    sample_tel=items[0].get('TELNO', '(없음)'),  # 🆕 TELNO 값 출력
                )

            records = []
            raw_rows = []

            for item in items:
                if not isinstance(item, dict):
                    continue

                # 영문 필드명 사용
                raw_name = str(item.get('BPLC_NM', '')).strip()
                raw_address = str(
                    item.get('ROAD_NM_ADDR', '') or item.get('LOTNO_ADDR', '')
                ).strip()

                if not raw_name or not raw_address:
                    continue

                status = str(item.get('SALS_STTS_NM', '')).strip()
                is_active = '폐업' not in status and '취소' not in status

                if not is_active:
                    continue

                p, m, e = parse_addr_tokens(raw_address)

                if province_prefixes and p and p not in province_prefixes:
                    continue

                raw_rows.append(item)

                # 좌표 변환 (영문 필드명)
                lat = lng = None
                x_val = item.get('CRD_INFO_X')
                y_val = item.get('CRD_INFO_Y')
                if x_val and y_val:
                    try:
                        lat, lng = self._convert_coordinates(float(x_val), float(y_val))
                    except (ValueError, TypeError):
                        pass

                # 위생등급
                grade_str = str(item.get('GRD_SE_NM', '')).strip()
                certs = [self.cert_type]
                
                # 🆕 위생등급 추출 (갑/을/병만)
                valid_grades = {'갑', '을', '병'}
                hygiene_grade = self._extract_hygiene_grade(grade_str)
                
                if hygiene_grade:
                    certs.append(f'hygiene_grade_{hygiene_grade}')

                record = CertRecord(
                    source=self.source,
                    cert_type=self.cert_type,
                    raw_name=raw_name,
                    raw_address=raw_address,
                    province=p,
                    municipality=m,
                    emd=e,
                    road_name=str(item.get('ROAD_NM_ADDR', '')).strip() or None,
                    lot_num=str(item.get('LOTNO_ADDR', '')).strip() or None,
                    lat=lat,
                    lng=lng,
                    phone=str(item.get('TELNO', '')).strip() or None,
                    biz_type=str(item.get('BZSTAT_SE_NM', '')).strip() or None,
                    designated_at=str(item.get('LCPMT_YMD', '')).strip() or None,
                    closed_at=str(item.get('CLSBIZ_YMD', '')).strip() or None,
                    is_active=is_active,
                    hygiene_grade=hygiene_grade,  # 🆕 별도 필드
                    certs=certs,
                    raw=item,
                )
                records.append(record)

            return records, total, raw_rows

        except Exception as e:
            logger.error('general_restaurant_parse_error', page=page, error=str(e))
            return [], 0, []

    def collect(self, province: Optional[str] = None) -> List[CertRecord]:
        """데이터 수집 (최대 페이지 제한)"""
        if not self.service_key:
            logger.warning('general_restaurant_skip_no_api_key')
            return []

        from .base import match_prefixes
        prefixes = match_prefixes(province) if province else ()

        logger.info(
            'general_restaurant_collect_start',
            province=province,
            prefixes=prefixes,
            max_pages=self.max_pages,
        )

        first_records, total, first_raw = self._fetch_page(1, prefixes)

        if not first_records and total == 0:
            logger.warning('general_restaurant_no_data', province=province)
            return []

        all_records = list(first_records)
        self.raw_rows.extend(first_raw)

        logger.info(
            'general_restaurant_first_page',
            records=len(first_records),
            total=total,
            max_pages=self.max_pages,
        )

        total_pages = (total // self.PAGE_SIZE) + 1
        actual_max_pages = min(self.max_pages, total_pages)

        if total_pages > self.max_pages:
            logger.warning(
                'general_restaurant_page_limit_applied',
                total_pages=total_pages,
                max_pages=self.max_pages,
            )

        if actual_max_pages <= 1:
            valid = [r for r in all_records if r.is_valid()]
            logger.info('general_restaurant_collect_complete', count=len(valid), total=total)
            return valid

        pages = list(range(2, actual_max_pages + 1))
        
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
                    logger.error('general_restaurant_page_failed', page=page, error=str(e))

                time.sleep(0.2)

        valid = [r for r in all_records if r.is_valid()]
        with_grade = sum(1 for r in valid if len(r.certs) > 1)
        
        logger.info(
            'general_restaurant_collect_complete',
            count=len(valid),
            total=total,
            with_hygiene_grade=with_grade,
            pages_fetched=actual_max_pages,
        )

        return valid