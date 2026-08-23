"""
Bareun Pipeline: Tourist Info Adapter Client
=============================================
- 한국관광공사(TourAPI) KorService2 HTTP 호출 전담
- 응답 JSON 파싱 (패턴 A/B 방어)
- 1,000건/일 쿼터 방어 및 Circuit Breaker 적용

수정 사항 (Autofix):
  - `callable` (내장 함수) → `typing.Callable` (타입 힌팅)로 수정
  - Pyright "Expected class but received function" 에러 해결

위치: src/bareun_pipeline/adapters/tourist_info/client.py
"""
from __future__ import annotations

import time
from typing import Any, Callable, Optional, Tuple
from urllib.parse import unquote

import requests

from ...observability.logging import get_logger
from ...settings import get_settings

logger = get_logger(__name__)


class TouristInfoClient:
    """
    관광공사 API 클라이언트 (HTTP + Parsing)
    
    BaseAdapter의 세션과 서킷 브레이커를 주입받아 사용합니다.
    이 클래스는 도메인 모델(CertRecord)을 알지 못하며, 오직 dict만 반환합니다.
    """

    BASE_URL: str = "https://apis.data.go.kr/B551011/KorService2"
    FOOD_CONTENT_TYPE_ID: int = 39

    # 법정동 시도 코드 (행정안전부 기준)
    LDONG_REGN_CODE_MAP: dict[str, int] = {
        '서울특별시': 11, '부산광역시': 26, '대구광역시': 27, '인천광역시': 28,
        '광주광역시': 29, '대전광역시': 30, '울산광역시': 31, '세종특별자치시': 36,
        '경기도': 41, '강원특별자치도': 42, '충청북도': 43, '충청남도': 44,
        '전북특별자치도': 45, '전라남도': 46, '경상북도': 47, '경상남도': 48,
        '제주특별자치도': 50,
    }

    def __init__(
        self,
        session: requests.Session,
        settings: Any,
        api_caller: Callable[..., Optional[requests.Response]],
    ):
        """
        Args:
            session: requests.Session (BaseAdapter가 생성)
            settings: get_settings() 인스턴스
            api_caller: BaseAdapter._safe_request와 동일한 시그니처의 콜백
        """
        self.session = session
        self.settings = settings
        self._api_caller = api_caller
        self.service_key = self._decode_key(settings.data_go_kr_key or '')

    @staticmethod
    def _decode_key(key: str) -> str:
        """URL 인코딩된 API 키 디코딩"""
        return unquote(key) if '%' in key else key

    # ── Public API ──────────────────────────────────────

    def fetch_list_page(
        self, 
        lDongRegnCd: int, 
        page: int, 
        num_of_rows: int = 100
    ) -> Tuple[list[dict], int]:
        """지역 기반 음식점 목록 단일 페이지 조회"""
        params = {
            'serviceKey': self.service_key,
            'MobileOS': 'ETC',
            'MobileApp': 'bareun',
            '_type': 'json',
            'lDongRegnCd': lDongRegnCd,
            'contentTypeId': self.FOOD_CONTENT_TYPE_ID,
            'pageNo': page,
            'numOfRows': num_of_rows,
            'arrange': 'A',
        }

        response = self._api_caller('GET', f'{self.BASE_URL}/areaBasedList2', params=params)
        if response is None or response.status_code != 200:
            self._log_http_error('list_page', page, response)
            return [], 0

        return self._parse_list_response(response, page)

    def fetch_detail_common(self, content_id: int) -> dict:
        """공통 정보 조회 (사진, 기본정보)"""
        return self._fetch_detail_endpoint('detailCommon2', content_id, {
            'defaultYN': 'Y', 'firstImageYN': 'Y', 'areacodeYN': 'Y',
            'catcodeYN': 'Y', 'addrinfoYN': 'Y', 'mapinfoYN': 'Y', 'overviewYN': 'Y',
        })

    def fetch_detail_intro(self, content_id: int) -> dict:
        """소개 정보 조회 (메뉴, 영업시간, 휴무일)"""
        return self._fetch_detail_endpoint('detailIntro2', content_id, {})

    # ── Private Helpers ─────────────────────────────────

    def _fetch_detail_endpoint(
        self, 
        endpoint: str, 
        content_id: int, 
        extra_params: dict
    ) -> dict:
        """상세 조회 공통 로직"""
        params = {
            'serviceKey': self.service_key,
            'MobileOS': 'ETC',
            'MobileApp': 'bareun',
            '_type': 'json',
            'contentId': content_id,
            'contentTypeId': self.FOOD_CONTENT_TYPE_ID,
            **extra_params,
        }

        response = self._api_caller('GET', f'{self.BASE_URL}/{endpoint}', params=params)
        if response is None or response.status_code != 200:
            self._log_http_error(endpoint, content_id, response)
            return {}

        try:
            data = response.json()
            items = data.get('response', {}).get('body', {}).get('items', {}).get('item', [])
            
            if not items:
                return {}
            if isinstance(items, list):
                return items[0]
            return items

        except Exception as e:
            logger.error(
                'tourist_info_parse_error', 
                endpoint=endpoint, content_id=content_id, error=str(e)
            )
            return {}

    def _parse_list_response(
        self, 
        response: requests.Response, 
        page: int
    ) -> Tuple[list[dict], int]:
        """
        목록 응답 파싱 (패턴 A/B 방어)
        - 패턴 A: {"response": {"header": {...}, "body": {...}}}
        - 패턴 B: {"resultCode": "...", "resultMsg": "...", "items": {...}}
        """
        try:
            data = response.json()
        except Exception as e:
            logger.error('tourist_info_json_decode_error', page=page, error=str(e))
            return [], 0

        result_code = None
        items = []
        total = 0

        # 패턴 A (중첩 구조)
        if 'response' in data:
            header = data['response'].get('header', {})
            result_code = header.get('resultCode', '')
            body = data['response'].get('body', {})
            total = body.get('totalCount', 0)
            items_container = body.get('items', {})
        # 패턴 B (플랫 구조)
        else:
            result_code = data.get('resultCode', '')
            total = data.get('totalCount', 0)
            items_container = data.get('items', {})

        # 결과 코드 검증
        if result_code and result_code != '00' and result_code != '0000':
            logger.error(
                'tourist_info_api_error',
                page=page,
                result_code=result_code,
                result_msg=data.get('resultMsg', ''),
            )
            return [], 0

        # 아이템 정규화
        if isinstance(items_container, dict):
            items = items_container.get('item', [])
        elif isinstance(items_container, list):
            items = items_container

        if not isinstance(items, list):
            items = [items] if items else []

        if page == 1:
            logger.info(
                'tourist_info_first_page_parsed',
                total_count=total,
                items_count=len(items),
            )

        return items, total

    def _log_http_error(
        self, 
        endpoint: str, 
        identifier: Any, 
        response: Optional[requests.Response]
    ) -> None:
        """HTTP 에러 로깅"""
        if response is None:
            logger.error('tourist_info_request_failed', endpoint=endpoint, id=identifier)
        else:
            logger.error(
                'tourist_info_http_error',
                endpoint=endpoint,
                id=identifier,
                status_code=response.status_code,
                text=response.text[:200] if response.text else '',
            )