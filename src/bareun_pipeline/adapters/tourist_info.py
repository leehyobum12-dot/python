"""
국문 관광정보 서비스 어댑터 (음식점)
=====================================
- 한국관광공사 제공 전국 단위 음식점 데이터
- 사진 (내부/외부), 메뉴, 영업시간, 휴무일, 예약 정보 포함

API 정보:
- URL: https://apis.data.go.kr/B551011/KorService2
- 일일 트래픽: 1,000건 (OPERATION별)
- 서비스:
  - /areaBasedList2: 지역기반 목록 조회
  - /detailCommon2: 공통정보 (사진 포함)
  - /detailIntro2: 소개정보 (메뉴, 영업시간, 휴무일)
  - /detailImage2: 이미지정보

주의: 일일 트래픽 1,000건이므로 상세 조회 건수 제한 필수!
"""
from __future__ import annotations

import time
import re
from typing import List, Optional, Dict, Tuple
from urllib.parse import unquote
from concurrent.futures import ThreadPoolExecutor, as_completed

from .base import BaseAdapter, parse_addr_tokens
from ..domain.record import CertRecord
from ..observability.logging import get_logger


logger = get_logger(__name__)


class TouristInfoAdapter(BaseAdapter):
    """
    국문 관광정보 서비스 어댑터 (음식점 특화)

    수집 정보:
    - 가게 이름, 주소, 전화번호
    - 사진 URL (내부/외부)
    - 취급 메뉴 (treatmenufood)
    - 영업시간 (opentimefood)
    - 쉬는 날 (restdatefood)
    - 예약 안내 (reservationfood)

    일일 트래픽: 1,000건
    - areaBasedList2: 목록 조회 (넉넉)
    - detailCommon2, detailIntro2: 상세 조회 (제한 필요)
    """

    source = 'tourist_info'
    cert_type = 'tourist_info'
    quota_provider = 'tourist_info'

    pii_fields = set()

    BASE_URL = 'https://apis.data.go.kr/B551011/KorService2'
    MAX_WORKERS = 4
    PAGE_SIZE = 100
    DETAIL_DELAY = 0.5  # 상세 조회 간 딜레이 (API 보호)

    # 기본 설정
    DEFAULT_MAX_DETAIL_COUNT = 100  # 기본 상세 조회 건수

    # 음식점 카테고리 코드
    FOOD_CONTENT_TYPE_ID = 39  # 음식

    # 지역 코드 매핑 (areaCode2 대신 하드코딩)
    AREA_CODE_MAP = {
        '서울특별시': 1,
        '인천광역시': 2,
        '대전광역시': 3,
        '대구광역시': 4,
        '광주광역시': 5,
        '부산광역시': 6,
        '울산광역시': 7,
        '세종특별자치시': 8,
        '경기도': 31,
        '강원특별자치도': 32,
        '충청북도': 33,
        '충청남도': 34,
        '경상북도': 35,
        '경상남도': 36,
        '전북특별자치도': 37,
        '전라남도': 38,
        '제주특별자치도': 39,
    }
    
    # 🆕 법정동 시도 코드 (행정안전부 기준)
    LDONG_REGN_CODE_MAP = {
        '서울특별시': 11,
        '부산광역시': 26,
        '대구광역시': 27,
        '인천광역시': 28,
        '광주광역시': 29,
        '대전광역시': 30,
        '울산광역시': 31,
        '세종특별자치시': 36,
        '경기도': 41,
        '강원특별자치도': 42,
        '충청북도': 43,
        '충청남도': 44,
        '전북특별자치도': 45,
        '전라남도': 46,
        '경상북도': 47,
        '경상남도': 48,
        '제주특별자치도': 50,
    }

    def __init__(
    self,
    fetch_details: bool = True,
    max_detail_count: int = 30,
    use_checkpoint: bool = True,  # 🆕 체크포인트 사용 여부
):
        """
        Args:
            fetch_details: 상세 정보 조회 여부
            max_detail_count: 상세 조회 최대 건수 (일일 트래픽 보호)
            use_checkpoint: 체크포인트 사용 (기존 데이터 스킵)
        """
        super().__init__()
        self.service_key = self.settings.data_go_kr_key or ''
        self._detail_count = 0
        self.fetch_details = fetch_details
        self.max_detail_count = max_detail_count
        self.use_checkpoint = use_checkpoint
        
        # 🆕 체크포인트 초기화
        from ..bronze.tourist_checkpoint import TouristCheckpoint
        self.checkpoint = TouristCheckpoint(self.settings.cache_dir)

    def _key(self) -> str:
        key = (self.service_key or '').strip()
        return unquote(key) if '%' in key else key

    # ═══════════════════════════════════════════════════════
    # 목록 조회 (areaBasedList2)
    # ═══════════════════════════════════════════════════════

    def _fetch_list_page(self, page: int, lDongRegnCd: int) -> Tuple[List[dict], int]:
        """
        음식점 목록 단일 페이지 조회 (KorService2)
        
        공식 문서 기준:
        - areaBasedList2에는 listYN 파라미터 없음
        - 응답은 플랫한 구조: resultCode, resultMsg, items 등
        """
        params = {
            'serviceKey': self._key(),
            'MobileOS': 'ETC',
            'MobileApp': 'bareun',
            '_type': 'json',
            'lDongRegnCd': lDongRegnCd,
            'contentTypeId': self.FOOD_CONTENT_TYPE_ID,
            'pageNo': page,
            'numOfRows': self.PAGE_SIZE,
            'arrange': 'A',  # 제목순 정렬
            # ❌ listYN 제거 (areaBasedList2에 없음)
        }

        response = self._safe_request('GET', f'{self.BASE_URL}/areaBasedList2', params=params)

        if response is None or response.status_code != 200:
            if response is not None:
                logger.error(
                    'tourist_info_list_failed',
                    page=page,
                    status_code=response.status_code,
                    response_text=response.text[:500] if response.text else '',
                )
            return [], 0

        try:
            data = response.json()
            
            # 디버깅: 응답 원본
            if page == 1:
                logger.info(
                    'tourist_info_raw_response',
                    keys=list(data.keys()),
                    raw_text=response.text[:800],
                )

            # 🆕 응답 구조 확인 (플랫 vs 중첩)
            # 패턴 A: {"response": {"header": {...}, "body": {...}}}
            # 패턴 B: {"resultCode": "...", "resultMsg": "...", "items": {...}}
            
            result_code = None
            result_msg = None
            items = []
            total = 0

            if 'response' in data:
                # 패턴 A: 중첩 구조
                response_wrapper = data['response']
                header = response_wrapper.get('header', {})
                result_code = header.get('resultCode', '')
                result_msg = header.get('resultMsg', '')
                body = response_wrapper.get('body', {})
                items_container = body.get('items', {})
                total = body.get('totalCount', 0)
                items = items_container.get('item', []) if isinstance(items_container, dict) else []
            else:
                # 패턴 B: 플랫 구조 (현재 응답 형태)
                result_code = data.get('resultCode', '')
                result_msg = data.get('resultMsg', '')
                total = data.get('totalCount', 0)
                items_container = data.get('items', {})
                if isinstance(items_container, dict):
                    items = items_container.get('item', [])
                elif isinstance(items_container, list):
                    items = items_container

            # 결과 코드 확인
            if result_code and result_code != '0000':
                logger.error(
                    'tourist_info_api_error',
                    result_code=result_code,
                    result_msg=result_msg or '',
                )
                return [], 0

            if not isinstance(items, list):
                items = [items] if items else []

            if page == 1:
                logger.info(
                    'tourist_info_response_structure',
                    total_count=total,
                    items_count=len(items),
                    result_code=result_code,
                )

            return items, total

        except Exception as e:
            logger.error('tourist_info_list_parse_error', page=page, error=str(e))
            return [], 0

    # ═══════════════════════════════════════════════════════
    # 상세 조회 (detailCommon2) - 사진, 기본정보
    # ═══════════════════════════════════════════════════════

    def _fetch_detail(self, content_id: int) -> dict:
        """음식점 상세 정보 조회 (detailCommon2)"""  # ✅ 수정
        params = {
            'serviceKey': self._key(),
            'MobileOS': 'ETC',
            'MobileApp': 'bareun',
            '_type': 'json',
            'contentId': content_id,
            'contentTypeId': self.FOOD_CONTENT_TYPE_ID,
            'defaultYN': 'Y',
            'firstImageYN': 'Y',
            'areacodeYN': 'Y',
            'catcodeYN': 'Y',
            'addrinfoYN': 'Y',
            'mapinfoYN': 'Y',
            'overviewYN': 'Y',
        }

        response = self._safe_request('GET', f'{self.BASE_URL}/detailCommon2', params=params)
        self._detail_count += 1

        if response is None or response.status_code != 200:
            return {}

        try:
            data = response.json()
            items = data.get('response', {}).get('body', {}).get('items', {}).get('item', [])

            if not items:
                return {}

            if not isinstance(items, list):
                items = [items]

            return items[0] if items else {}

        except Exception as e:
            logger.error('tourist_info_detail_error', content_id=content_id, error=str(e))
            return {}

    # ═══════════════════════════════════════════════════════
    # 소개 정보 조회 (detailIntro2) - 메뉴, 영업시간, 휴무일
    # ═══════════════════════════════════════════════════════

    def _fetch_intro_info(self, content_id: int) -> dict:
        """음식점 소개 정보 조회 (detailIntro2)"""  # ✅ 수정
        params = {
            'serviceKey': self._key(),
            'MobileOS': 'ETC',
            'MobileApp': 'bareun',
            '_type': 'json',
            'contentId': content_id,
            'contentTypeId': self.FOOD_CONTENT_TYPE_ID,
        }

        response = self._safe_request('GET', f'{self.BASE_URL}/detailIntro2', params=params)
        self._detail_count += 1

        if response is None or response.status_code != 200:
            return {}

        try:
            data = response.json()
            items = data.get('response', {}).get('body', {}).get('items', {}).get('item', [])

            if not items:
                return {}

            if not isinstance(items, list):
                items = [items]

            return items[0] if items else {}

        except Exception as e:
            logger.error('tourist_info_intro_error', content_id=content_id, error=str(e))
            return {}

    # ═══════════════════════════════════════════════════════
    # 파싱 로직
    # ═══════════════════════════════════════════════════════

    def _parse_menus(self, intro: dict) -> Tuple[Optional[str], List[str]]:
        """
        메뉴 파싱 (음식점 전용)
        
        공식 문서 (contentTypeId=39):
        - firstmenu: 대표메뉴
        - treatmenu: 취급메뉴
        
        Returns:
            (main_menu, menus)
        """
        main_menu = None
        menus = []
        
        # 대표메뉴 (firstmenu)
        first_menu = intro.get('firstmenu', '').strip()
        if first_menu and 2 <= len(first_menu) <= 50:
            main_menu = first_menu
        
        # 취급메뉴 (treatmenu)
        treat_menu = intro.get('treatmenu', '').strip()
        if treat_menu:
            parts = re.split(r'[,;/|\n·]', treat_menu)
            for part in parts:
                part = part.strip()
                part = re.sub(r'<[^>]+>', '', part).strip()
                part = re.sub(r'\s*\d+[,.]?\d*원?\s*$', '', part).strip()
                
                if part and 2 <= len(part) <= 50:
                    menus.append(part)
        
        # main_menu가 없으면 menus의 첫 번째 항목 사용
        if not main_menu and menus:
            main_menu = menus[0]
        
        # 중복 제거 (main_menu는 menus에서 제거)
        if main_menu and main_menu in menus:
            menus.remove(main_menu)
        
        return main_menu, menus[:20]

    def _parse_business_hours(self, open_time: str) -> Dict[str, str]:
        """영업시간 파싱"""
        if not open_time:
            return {}

        result = {}
        match = re.search(r'(\d{1,2}:\d{2})\s*[-~]\s*(\d{1,2}:\d{2})', open_time)
        if match:
            result['daily'] = f"{match.group(1)}-{match.group(2)}"

        return result

    def _parse_rest_days(self, rest_days: str) -> List[str]:
        """휴무일 파싱"""
        if not rest_days:
            return []

        DAY_MAP = {
            '월': 'monday', '화': 'tuesday', '수': 'wednesday',
            '목': 'thursday', '금': 'friday', '토': 'saturday', '일': 'sunday',
        }

        days = []
        for korean_day, english_day in DAY_MAP.items():
            if korean_day in rest_days:
                days.append(english_day)

        return days

    def _extract_photos(self, item: dict, detail: dict = None) -> List[Dict[str, str]]:
        """
        사진 URL 추출
        
        Args:
            item: 목록 응답 아이템
            detail: 상세 조회 응답 (선택)
        """
        photos = []
        
        # 1. 목록 응답에서 추출 (firstimage, firstimage2)
        first_image = item.get('firstimage', '').strip()
        if first_image:
            photos.append({'url': first_image, 'category': 'exterior'})
        
        second_image = item.get('firstimage2', '').strip()
        if second_image:
            photos.append({'url': second_image, 'category': 'interior'})
        
        # 2. 상세 조회 응답에서도 추출 (추가 이미지)
        if detail:
            detail_first = detail.get('firstimage', '').strip()
            if detail_first and detail_first not in [p['url'] for p in photos]:
                photos.append({'url': detail_first, 'category': 'exterior'})
            
            detail_second = detail.get('firstimage2', '').strip()
            if detail_second and detail_second not in [p['url'] for p in photos]:
                photos.append({'url': detail_second, 'category': 'interior'})
        
        return photos

    # ═══════════════════════════════════════════════════════
    # 메인 수집 로직
    # ═══════════════════════════════════════════════════════

    def collect(self, province: Optional[str] = None) -> List[CertRecord]:
        """데이터 수집 (BaseAdapter 시그니처 준수)"""
        fetch_details = self.fetch_details
        max_detail_count = self.max_detail_count

        if not self.service_key:
            logger.warning('tourist_info_skip_no_api_key')
            return []

        # 🆕 법정동 시도 코드 사용
        lDongRegnCd = self.LDONG_REGN_CODE_MAP.get(province)
        if not lDongRegnCd:
            logger.warning('tourist_info_no_ldong_code', province=province)
            return []

        # 🆕 기존 content_id 로드
        existing_ids = self.checkpoint.get_existing_ids() if self.use_checkpoint else set()

        logger.info(
            'tourist_info_collect_start',
            province=province,
            lDongRegnCd=lDongRegnCd,
            fetch_details=fetch_details,
            max_detail_count=max_detail_count,
            daily_traffic_limit=1000,
            use_checkpoint=self.use_checkpoint,
            existing_count=len(existing_ids),
        )

        self._detail_count = 0

        all_items = []
        new_items = []  # 🆕 신규 아이템만 추적
        page = 1

        while True:
            # 🆕 lDongRegnCd 전달
            items, total = self._fetch_list_page(page, lDongRegnCd)

            if not items:
                break

            all_items.extend(items)
            
            # 🆕 신규 아이템 필터링
            for item in items:
                content_id = int(item.get('contentid', 0))
                if content_id not in existing_ids:
                    new_items.append(item)
            
            logger.info(
                'tourist_info_list_progress',
                page=page,
                collected=len(all_items),
                new_items=len(new_items),
                total=total,
            )

            if len(all_items) >= total or page >= 10:
                break

            page += 1
            time.sleep(0.3)

        if not all_items:
            logger.warning('tourist_info_no_items', province=province)
            return []

        # 🆕 신규 아이템이 없으면 조기 종료
        if self.use_checkpoint and not new_items:
            logger.info(
                'tourist_info_no_new_items',
                total_fetched=len(all_items),
                all_existing=True,
            )
            return []

        # 🆕 신규 아이템만 상세 조회
        detail_items = new_items[:max_detail_count] if fetch_details else []
        details_cache: Dict[int, dict] = {}

        if detail_items:
            logger.info(
                'tourist_info_detail_start',
                count=len(detail_items),
                estimated_traffic_usage=len(detail_items) * 2,
            )

            for i, item in enumerate(detail_items):
                content_id = item.get('contentid')
                if not content_id:
                    continue

                # 상세 정보 조회
                detail = self._fetch_detail(int(content_id))
                intro = self._fetch_intro_info(int(content_id))

                details_cache[int(content_id)] = {
                    'detail': detail,
                    'intro': intro,
                }

                if (i + 1) % 10 == 0:
                    logger.info(
                        'tourist_info_detail_progress',
                        processed=i + 1,
                        total=len(detail_items),
                        traffic_used=self._detail_count,
                    )

                time.sleep(self.DETAIL_DELAY)

        # CertRecord 변환 (🆕 신규 아이템만)
        records = []
        new_content_ids = []
        
        for item in new_items:  # 🆕 all_items → new_items
            content_id = int(item.get('contentid', 0))

            raw_name = item.get('title', '').strip()
            addr1 = item.get('addr1', '').strip()
            addr2 = item.get('addr2', '').strip()
            raw_address = f"{addr1} {addr2}".strip()

            if not raw_name or not raw_address:
                continue

            p, m, e = parse_addr_tokens(raw_address)

            # 상세 정보 병합
            detail_info = details_cache.get(content_id, {})
            detail = detail_info.get('detail', {})
            intro = detail_info.get('intro', {})

            # 🆕 메뉴 파싱 (수정된 시그니처)
            main_menu, menus = self._parse_menus(intro)

            # 영업시간 파싱
            open_time = intro.get('opentimefood', '')
            business_hours = self._parse_business_hours(open_time)

            # 휴무일 파싱
            rest_days_str = intro.get('restdatefood', '')
            rest_days = self._parse_rest_days(rest_days_str)

            # 🆕 사진 추출 (수정된 시그니처 - 목록 응답도 포함)
            photos = self._extract_photos(item, detail)

            # 좌표
            lat = lng = None
            mapy = item.get('mapy')
            mapx = item.get('mapx')
            if mapy and mapx:
                try:
                    lat = float(mapy)
                    lng = float(mapx)
                except (ValueError, TypeError):
                    pass

            # external_info 구성
            external_info = {
                'content_id': content_id,
                'business_hours': business_hours,
                'rest_days': rest_days,
                'photos': photos,
                'tel': detail.get('tel', '').strip() or item.get('tel', '').strip(),
                'homepage': detail.get('homepage', '').strip(),
                'source': 'tourist_info',
            }

            record = CertRecord(
                source=self.source,
                cert_type=self.cert_type,
                raw_name=raw_name,
                raw_address=raw_address,
                province=p,
                municipality=m,
                emd=e,
                road_name=addr1 or None,
                lot_num=addr2 or None,
                lat=lat,
                lng=lng,
                phone=detail.get('tel', '').strip() or item.get('tel', '').strip() or None,
                main_menu=main_menu,
                main_menu_source='tourist_info' if main_menu else None,
                menus=menus,
                menus_source='tourist_info' if menus else None,
                is_active=True,
                raw={
                    'content_id': content_id,
                    'external_info': external_info,
                },
            )
            records.append(record)
            new_content_ids.append(content_id)
            
            self.raw_rows.append({
                'content_id': content_id,
                'title': raw_name,
                'addr': raw_address,
                'main_menu': main_menu,
                'menus': menus,
                'business_hours': business_hours,
                'rest_days': rest_days,
                'photos': photos,
            })

        # 🆕 체크포인트 업데이트 (신규 content_id만)
        if self.use_checkpoint and new_content_ids:
            self.checkpoint.update(new_content_ids)
        
        # 🆕 체크포인트 통계 출력
        if self.use_checkpoint:
            stats = self.checkpoint.get_stats()
            logger.info(
                'tourist_info_checkpoint_stats',
                total_count=stats['total_count'],
                last_update=stats['last_update'],
            )

        logger.info(
            'tourist_info_collect_complete',
            count=len(records),
            new_count=len(new_items),
            skipped_existing=len(existing_ids),
            with_details=len(details_cache),
            total_detail_api_calls=self._detail_count,
        )

        return records