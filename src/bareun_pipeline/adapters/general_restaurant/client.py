# ==============================================================================
# 파일 3: src/bareun_pipeline/adapters/general_restaurant/client.py
# ==============================================================================
"""HTTP 클라이언트 (페이징 전담)"""

from __future__ import annotations

from collections.abc import Callable

from ...observability.logging import get_logger

logger = get_logger(__name__)


class GeneralRestaurantClient:
    BASE_URL = 'https://apis.data.go.kr/1741000/general_restaurants/info'

    def __init__(self, service_key: str, api_caller: Callable):
        self.service_key = service_key
        self._api_caller = api_caller

    def fetch_page(self, page: int, page_size: int = 500) -> tuple[list[dict], int]:
        params = {
            'serviceKey': self.service_key,
            'pageNo': page,
            'numOfRows': page_size,
            'type': 'json',
        }
        response = self._api_caller('GET', self.BASE_URL, params=params)

        if response is None or response.status_code != 200:
            return [], 0

        try:
            data = response.json()
            body = data.get('response', {}).get('body', {})
            total = body.get('totalCount', 0)
            items_container = body.get('items', [])

            items = (
                items_container.get('item', [])
                if isinstance(items_container, dict)
                else items_container
            )
            return (items if isinstance(items, list) else [items]), total
        except Exception as e:
            logger.error('general_restaurant_parse_error', page=page, error=str(e))
            return [], 0
