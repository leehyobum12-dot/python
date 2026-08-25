"""
Bareun Pipeline: Tourist Info Adapter Client
=============================================
- 한국관광공사(TourAPI) KorService2 HTTP 호출 전담
- 응답 JSON 파싱 (패턴 A/B 방어)
- 1,000건/일 쿼터 방어 및 Circuit Breaker 적용
- 🆕 QuotaManager 통합 (사전 차단 + 경고 로깅)
- 🆕 Type Hardening (dict → dict[str, Any] 명시)

위치: src/bareun_pipeline/adapters/tourist_info/client.py
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import unquote

import requests

from ...observability.logging import get_logger
from ...observability.quota import QuotaManager

logger = get_logger(__name__)


class TouristInfoClient:
    """
    관광공사 API 클라이언트 (HTTP + Parsing + Quota)

    BaseAdapter의 세션과 서킷 브레이커를 주입받아 사용합니다.
    이 클래스는 도메인 모델(CertRecord)을 알지 못하며, 오직 dict만 반환합니다.
    """

    BASE_URL: str = 'https://apis.data.go.kr/B551011/KorService2'
    FOOD_CONTENT_TYPE_ID: int = 39

    LDONG_REGN_CODE_MAP: dict[str, int] = {
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
        session: requests.Session,
        settings: Any,
        api_caller: Callable[..., requests.Response | None],
    ):
        self.session = session
        self.settings = settings
        self._api_caller = api_caller
        self.service_key = self._decode_key(settings.data_go_kr_key or '')
        self._quota = QuotaManager.get_instance()

    @staticmethod
    def _decode_key(key: str) -> str:
        return unquote(key) if '%' in key else key

    # ── Public API ──────────────────────────────────────

    def fetch_list_page(
        self,
        lDongRegnCd: int,
        page: int,
        num_of_rows: int = 100,
    ) -> tuple[list[dict[str, Any]], int]:
        """
        지역 기반 음식점 목록 단일 페이지 조회

        쿼터 소비: 1건/호출
        """
        self._quota.check_and_consume('tourist_info', count=1)

        params: dict[str, Any] = {
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

        response = self._api_caller(
            'GET',
            f'{self.BASE_URL}/areaBasedList2',
            params=params,
        )
        if response is None or response.status_code != 200:
            self._log_http_error('list_page', page, response)
            return [], 0

        return self._parse_list_response(response, page)

    def fetch_detail_common(self, content_id: int) -> dict[str, Any]:
        """공통 정보 조회 (사진, 기본정보)"""
        return self._fetch_detail_endpoint(
            'detailCommon2',
            content_id,
            {
                'defaultYN': 'Y',
                'firstImageYN': 'Y',
                'areacodeYN': 'Y',
                'catcodeYN': 'Y',
                'addrinfoYN': 'Y',
                'mapinfoYN': 'Y',
                'overviewYN': 'Y',
            },
        )

    def fetch_detail_intro(self, content_id: int) -> dict[str, Any]:
        """소개 정보 조회 (메뉴, 영업시간, 휴무일)"""
        return self._fetch_detail_endpoint('detailIntro2', content_id, {})

    # ── Private Helpers ─────────────────────────────────

    def _fetch_detail_endpoint(
        self,
        endpoint: str,
        content_id: int,
        extra_params: dict[str, str],
    ) -> dict[str, Any]:
        """상세 조회 공통 로직 (쿼터 1건 소비)"""
        self._quota.check_and_consume('tourist_info', count=1)

        params: dict[str, Any] = {
            'serviceKey': self.service_key,
            'MobileOS': 'ETC',
            'MobileApp': 'bareun',
            '_type': 'json',
            'contentId': content_id,
            'contentTypeId': self.FOOD_CONTENT_TYPE_ID,
            **extra_params,
        }

        # 🐛 수정: timeout 제거 (BaseAdapter._safe_request가 이미 처리)
        response = self._api_caller(
            'GET',
            f'{self.BASE_URL}/{endpoint}',
            params=params,
        )
        if response is None or response.status_code != 200:
            self._log_http_error(endpoint, content_id, response)
            return {}

        try:
            data: dict[str, Any] = response.json()
            body = data.get('response', {}).get('body', {})
            items_container = body.get('items', {})

            header = data.get('response', {}).get('header', {})
            result_code = str(header.get('resultCode', ''))

            if result_code and result_code not in ('00', '0000'):
                logger.warning(
                    'tourist_info_detail_api_warning',
                    endpoint=endpoint,
                    content_id=content_id,
                    result_code=result_code,
                    result_msg=str(header.get('resultMsg', '')),
                )

            raw_items: Any
            if isinstance(items_container, dict):
                raw_items = items_container.get('item', [])
            else:
                raw_items = items_container

            items: list[dict[str, Any]]
            if isinstance(raw_items, list):
                items = [i for i in raw_items if isinstance(i, dict)]
            elif isinstance(raw_items, dict):
                items = [raw_items]
            else:
                items = []

            if not items:
                logger.debug(
                    'tourist_info_detail_empty_response',
                    endpoint=endpoint,
                    content_id=content_id,
                    result_code=result_code,
                )
                return {}

            return items[0]

        except Exception as e:
            logger.error(
                'tourist_info_parse_error',
                endpoint=endpoint,
                content_id=content_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            return {}

    def _parse_list_response(
        self,
        response: requests.Response,
        page: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """목록 응답 파싱 (패턴 A/B 방어)"""
        try:
            data: dict[str, Any] = response.json()
        except Exception as e:
            logger.error(
                'tourist_info_json_decode_error',
                page=page,
                error=str(e),
            )
            return [], 0

        result_code: str = ''
        items: list[dict[str, Any]] = []
        total: int = 0
        # 🆕 items_container 타입을 Any로 명시 (dict | list | None 방어)
        items_container: Any = None

        # 패턴 A (중첩 구조)
        if 'response' in data:
            response_obj: dict[str, Any] = data['response']
            header: dict[str, Any] = response_obj.get('header', {})
            result_code = str(header.get('resultCode', ''))
            body: dict[str, Any] = response_obj.get('body', {})
            total = int(body.get('totalCount', 0))
            items_container = body.get('items', {})
        # 패턴 B (플랫 구조)
        else:
            result_code = str(data.get('resultCode', ''))
            total = int(data.get('totalCount', 0))
            items_container = data.get('items', {})

        # 결과 코드 검증
        if result_code and result_code not in ('00', '0000'):
            logger.error(
                'tourist_info_api_error',
                page=page,
                result_code=result_code,
                result_msg=str(data.get('resultMsg', '')),
            )
            return [], 0

        # 🆕 아이템 정규화 (명시적 타입 캐스팅 + isinstance 체크)
        raw_items: Any
        if isinstance(items_container, dict):
            raw_items = items_container.get('item', [])
        else:
            raw_items = items_container

        if isinstance(raw_items, list):
            items = [i for i in raw_items if isinstance(i, dict)]
        elif isinstance(raw_items, dict):
            items = [raw_items]
        else:
            items = []

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
        response: requests.Response | None,
    ) -> None:
        """HTTP 에러 로깅"""
        if response is None:
            logger.error(
                'tourist_info_request_failed',
                endpoint=endpoint,
                id=identifier,
            )
        else:
            logger.error(
                'tourist_info_http_error',
                endpoint=endpoint,
                id=identifier,
                status_code=response.status_code,
                text=response.text[:200] if response.text else '',
            )
