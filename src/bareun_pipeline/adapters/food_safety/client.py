"""
Bareun Pipeline: Food Safety Adapter Client
============================================
- data.go.kr 및 식품안전나라(MFDS) HTTP 호출 전담
- 병렬 페이지 수집 (ThreadPoolExecutor)
- 지수 백오프 재시도 (INFO-500 대응)
- 조기 종료 (Early Termination) 로직 포함

위치: src/bareun_pipeline/adapters/food_safety/client.py
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Optional, Tuple

import requests

from ...observability.logging import get_logger
from ...settings import get_settings

logger = get_logger(__name__)


class FoodSafetyClient:
    """
    식품안전업소 API 클라이언트 (HTTP + Paging + Retry)
    
    BaseAdapter의 세션과 서킷 브레이커를 콜백으로 주입받아 사용합니다.
    이 클래스는 도메인 모델(CertRecord)을 알지 못하며, 오직 dict 리스트만 반환합니다.
    """

    MFDS_URL_TPL = "http://openapi.foodsafetykorea.go.kr/api/{key}/C004/json/{start}/{end}"
    
    # 수집 설정
    PAGE_SIZE = 300
    PARALLEL_PAGE_WORKERS = 6
    PAGE_MAX_RETRIES = 3
    PAGE_RETRY_DELAY = 1.0
    MAX_TOTAL_PAGES = 200
    MAX_CONSECUTIVE_FAILURES = 5  # 🐛 버그 수정: 기존 코드에서 미정의된 속성 참조 해결

    def __init__(
        self,
        settings: Any,
        api_caller: Callable[..., Optional[requests.Response]],
    ):
        self.settings = settings
        self._api_caller = api_caller
        self.mfds_key = self._decode_key(settings.foodsafety_korea_key or "")
        self.datago_key = self._decode_key(settings.data_go_kr_key or "")

    @staticmethod
    def _decode_key(key: str) -> str:
        """URL 인코딩된 API 키 디코딩"""
        from urllib.parse import unquote
        return unquote(key) if "%" in key else key

    # ── data.go.kr 수집 ──────────────────────────────────────

    def fetch_datago(self, url: str) -> list[dict[str, Any]]:
        """data.go.kr 미러에서 페이징 수집"""
        all_items = []
        page = 1

        while True:
            params = {
                "serviceKey": self.datago_key,
                "pageNo": page,
                "numOfRows": self.PAGE_SIZE,
                "type": "json",
            }

            response = self._api_caller("GET", url, params=params)
            if response is None or response.status_code != 200:
                self._log_http_error("datago", page, response)
                break

            try:
                data = response.json()
                body = (data.get("response") or {}).get("body") or {}
                items = self._normalize_items(body.get("items"))

                if not items:
                    break

                all_items.extend(items)

                if len(items) < self.PAGE_SIZE:
                    break

                page += 1
                time.sleep(0.4)

            except Exception as e:
                logger.error("food_safety_datago_parse_error", page=page, error=str(e))
                break

        return all_items

    # ── 식품안전나라 (MFDS) 수집 ─────────────────────────────

    def fetch_mfds(self) -> list[dict[str, Any]]:
        """식품안전나라 병렬 수집 (지수 백오프 + 조기 종료)"""
        start_time = time.time()

        # 1. 첫 페이지 조회 (전체 페이지 수 계산용)
        first_result = self._fetch_mfds_page_with_retry(1, self.PAGE_SIZE)
        if not first_result:
            logger.warning("food_safety_mfds_first_page_failed")
            return []

        first_items, total_count = first_result

        if not first_items:
            logger.info("food_safety_mfds_empty")
            return []

        if total_count > 0:
            total_pages = min((total_count // self.PAGE_SIZE) + 1, self.MAX_TOTAL_PAGES)
        else:
            total_pages = self.MAX_TOTAL_PAGES

        logger.info("food_safety_mfds_start", total=total_count, pages=total_pages)

        if total_pages <= 1:
            return first_items

        # 2. 나머지 페이지 병렬 수집
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

                        # 🐛 버그 수정: MAX_CONSECUTIVE_FAILURES 속성 참조 안정화
                        if consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
                            logger.warning(
                                "food_safety_mfds_early_termination",
                                consecutive_failures=consecutive_failures,
                                remaining=total_pages - success_pages - failed_pages,
                            )
                            for f in futures:
                                if not f.done():
                                    f.cancel()
                            break

                except Exception as e:
                    logger.error("food_safety_mfds_future_error", page=page, error=str(e))
                    failed_pages += 1
                    consecutive_failures += 1

        elapsed = time.time() - start_time
        logger.info(
            "food_safety_mfds_complete",
            items=len(all_items),
            success=success_pages + 1,
            failed=failed_pages,
            elapsed=round(elapsed, 1),
        )

        return all_items

    def _fetch_mfds_page_with_retry(
        self, page: int, page_size: int
    ) -> Optional[Tuple[list, int]]:
        """단일 페이지 수집 (지수 백오프 재시도)"""
        for attempt in range(1, self.PAGE_MAX_RETRIES + 1):
            result = self._fetch_mfds_page(page, page_size)

            if result is not None:
                return result

            if attempt < self.PAGE_MAX_RETRIES:
                wait_time = self.PAGE_RETRY_DELAY * (2 ** (attempt - 1))
                logger.debug(
                    "food_safety_mfds_page_retry",
                    page=page,
                    attempt=attempt,
                    wait_seconds=wait_time,
                )
                time.sleep(wait_time)

        logger.warning("food_safety_mfds_page_failed", page=page, max_retries=self.PAGE_MAX_RETRIES)
        return None

    def _fetch_mfds_page(self, page: int, page_size: int) -> Optional[Tuple[list, int]]:
        """단일 페이지 수집 (재시도 없음)"""
        start = (page - 1) * page_size + 1
        end = page * page_size
        url = self.MFDS_URL_TPL.format(key=self.mfds_key, start=start, end=end)

        response = self._api_caller("GET", url)
        if response is None or response.status_code != 200:
            self._log_http_error("mfds", page, response)
            return None

        try:
            response.encoding = "utf-8"
            data = response.json()
            payload = data.get("C004", {})
            result = payload.get("RESULT")

            code = ""
            if isinstance(result, list) and result:
                code = (result[0] or {}).get("CODE", "")
            elif isinstance(result, dict):
                code = result.get("CODE", "")

            if code == "ERROR-500":
                logger.error("food_safety_mfds_error_500", page=page)
                return None

            if code == "INFO-500":
                logger.debug("food_safety_mfds_info_500", page=page)
                return None

            if code == "INFO-200":
                logger.debug("food_safety_mfds_no_more_data", page=page)
                return [], 0

            if code not in (None, "", "INFO-000"):
                logger.warning("food_safety_mfds_fatal_code", page=page, code=code)
                return None

            rows = payload.get("row", [])
            if isinstance(rows, dict):
                rows = [rows]

            if not rows:
                return [], 0

            total_count = 0
            if page == 1:
                total_count = payload.get("TOTAL_COUNT", 0)
                if not total_count and isinstance(result, list) and result:
                    total_count = (result[0] or {}).get("TOTAL_COUNT", 0)
                if isinstance(total_count, str):
                    try:
                        total_count = int(total_count)
                    except (ValueError, TypeError):
                        total_count = 0

            return rows, total_count

        except Exception as e:
            logger.error("food_safety_mfds_parse_error", page=page, error=str(e))
            return None

    # ── Helpers ──────────────────────────────────────────

    def _normalize_items(self, items: Any) -> list:
        """items 정규화"""
        if items in (None, "", [], {}):
            return []
        if isinstance(items, dict):
            items = items.get("item", [])
        if items in (None, ""):
            return []
        if isinstance(items, dict):
            return [items]
        return items if isinstance(items, list) else []

    def _log_http_error(
        self, vendor: str, page: int, response: Optional[requests.Response]
    ) -> None:
        """HTTP 에러 로깅"""
        if response is None:
            logger.error("food_safety_request_failed", vendor=vendor, page=page)
        else:
            logger.error(
                "food_safety_http_error",
                vendor=vendor,
                page=page,
                status_code=response.status_code,
                text=response.text[:200] if response.text else "",
            )