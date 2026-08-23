"""
Bareun Pipeline: Local Search Adapter Client
=============================================
- 네이버 로컬 검색 + 카카오 키워드 검색 어댑터
- 가게명 검증, 메뉴 보강, 정보 보강에서 공용 사용
- Circuit Breaker 및 재시도 로직 포함

이관 출처: silver/enrich.py (StoreNameVerifier, MenuEnricher, StoreInfoEnricher의 API 호출 부분)
"""
from __future__ import annotations

import re
from typing import Any, Optional

import httpx

from ..observability.logging import get_logger
from ..settings import get_settings
from .base import CircuitBreaker, CircuitOpenError

logger = get_logger(__name__)


class LocalSearchAdapter:
    """네이버/카카오 로컬 검색 어댑터"""

    NAVER_SEARCH_URL: str = 'https://openapi.naver.com/v1/search/local.json'
    KAKAO_SEARCH_URL: str = 'https://dapi.kakao.com/v2/local/search/keyword.json'

    def __init__(self, settings=None):
        self.settings = settings or get_settings()
        self.circuit_breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=300)

    # ── 네이버 로컬 검색 ──────────────────────────────────────

    async def search_naver(
        self,
        query: str,
        client: httpx.AsyncClient,
        display: int = 3,
    ) -> Optional[str]:
        """네이버 로컬 검색에서 첫 번째 결과의 제목 반환 (가게명 검증용)"""
        items = await self._search_naver_raw(query, client, display)
        if not items:
            return None

        for item in items:
            title = re.sub(r'<[^>]+>', '', item.get('title', '')).strip()
            if title and len(title) > 2:
                return title
        return None

    async def search_naver_items(
        self,
        query: str,
        client: httpx.AsyncClient,
        display: int = 5,
    ) -> list[dict[str, Any]]:
        """네이버 로컬 검색 결과 전체 반환 (메뉴/정보 보강용)"""
        return await self._search_naver_raw(query, client, display)

    async def _search_naver_raw(
        self,
        query: str,
        client: httpx.AsyncClient,
        display: int,
    ) -> list[dict[str, Any]]:
        """네이버 로컬 검색 원본 호출"""
        if self.circuit_breaker.is_open:
            return []

        cid = self.settings.naver_client_id
        csec = self.settings.naver_client_secret

        if not cid or not csec:
            return []

        try:
            response = await client.get(
                self.NAVER_SEARCH_URL,
                headers={
                    'X-Naver-Client-Id': cid,
                    'X-Naver-Client-Secret': csec,
                },
                params={'query': query, 'display': display, 'start': 1},
                timeout=10.0,
            )

            if response.status_code == 200:
                self.circuit_breaker._on_success()
                data = response.json()
                return data.get('items', [])

            elif response.status_code == 429:
                self.circuit_breaker._on_failure()
                logger.warning('local_search_naver_rate_limit', query=query)
            else:
                self.circuit_breaker._on_failure()

        except CircuitOpenError:
            return []
        except Exception as e:
            logger.error('local_search_naver_error', query=query, error=str(e))
            self.circuit_breaker._on_failure()

        return []

    # ── 카카오 키워드 검색 ──────────────────────────────────────

    async def search_kakao(
        self,
        query: str,
        client: httpx.AsyncClient,
        size: int = 3,
    ) -> Optional[str]:
        """카카오 키워드 검색에서 첫 번째 결과의 장소명 반환 (가게명 검증용)"""
        documents = await self._search_kakao_raw(query, client, size)
        if not documents:
            return None

        for doc in documents:
            place_name = doc.get('place_name', '').strip()
            if place_name and len(place_name) > 2:
                return place_name
        return None

    async def search_kakao_documents(
        self,
        query: str,
        client: httpx.AsyncClient,
        size: int = 5,
    ) -> list[dict[str, Any]]:
        """카카오 키워드 검색 결과 전체 반환 (메뉴/정보 보강용)"""
        return await self._search_kakao_raw(query, client, size)

    async def _search_kakao_raw(
        self,
        query: str,
        client: httpx.AsyncClient,
        size: int,
    ) -> list[dict[str, Any]]:
        """카카오 키워드 검색 원본 호출"""
        if self.circuit_breaker.is_open:
            return []

        kakao_key = self.settings.kakao_rest_api_key

        if not kakao_key:
            return []

        try:
            response = await client.get(
                self.KAKAO_SEARCH_URL,
                headers={'Authorization': f'KakaoAK {kakao_key}'},
                params={'query': query, 'size': size, 'page': 1},
                timeout=10.0,
            )

            if response.status_code == 200:
                self.circuit_breaker._on_success()
                data = response.json()
                return data.get('documents', [])

            elif response.status_code == 429:
                self.circuit_breaker._on_failure()
                logger.warning('local_search_kakao_rate_limit', query=query)
            else:
                self.circuit_breaker._on_failure()

        except CircuitOpenError:
            return []
        except Exception as e:
            logger.error('local_search_kakao_error', query=query, error=str(e))
            self.circuit_breaker._on_failure()

        return []