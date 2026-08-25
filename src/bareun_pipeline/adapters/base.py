"""
Bareun Pipeline: Adapter Base (Slim + Hard Timeout)
=====================================================
- HTTP 세션 관리, Circuit Breaker, BaseAdapter 추상 클래스
- 🆕 Hard Timeout: ThreadPoolExecutor로 무한 대기 방지
- 주소 유틸리티는 domain/address.py로 이관됨 (대안 B 확정)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from datetime import datetime
from typing import Any, TypeVar

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ── 주소 유틸: domain.address에서 re-export (하위 호환성) ──
from ..domain.address import (
    ADDR_PATTERN,
    PROVINCE_CANON,
    SIDO_ALIAS,
    canon_province,
    extract_emd_from_addr,
    is_emd_token,
    is_road_name,
    match_prefixes,
    norm_addr,
    norm_name,
    parse_addr_tokens,
)
from ..domain.record import CertRecord
from ..observability.logging import get_logger
from ..settings import get_settings

logger = get_logger(__name__)
T = TypeVar('T')

__all__ = [
    # Address utilities
    'ADDR_PATTERN',
    'PROVINCE_CANON',
    'SIDO_ALIAS',
    'canon_province',
    'extract_emd_from_addr',
    'is_emd_token',
    'is_road_name',
    'match_prefixes',
    'norm_addr',
    'norm_name',
    'parse_addr_tokens',
    # Adapter infrastructure
    'CircuitOpenError',
    'CircuitBreaker',
    'create_session',
    'BaseAdapter',
]


# ═══════════════════════════════════════════════════════════
# Circuit Breaker (기존과 동일)
# ═══════════════════════════════════════════════════════════


class CircuitOpenError(Exception):
    """Circuit이 열려 있어 호출 불가할 때 발생"""

    pass


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 300):
        self._failure_count = 0
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._last_failure_time: datetime | None = None
        self._is_open = False

    @property
    def is_open(self) -> bool:
        return self._is_open

    def call(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        if self._is_open:
            if self._last_failure_time:
                elapsed = (datetime.now() - self._last_failure_time).total_seconds()
                if elapsed < self._recovery_timeout:
                    raise CircuitOpenError(
                        f'Circuit is open. Wait {self._recovery_timeout - elapsed:.0f}s'
                    )
                self._is_open = False
                self._failure_count = 0
                logger.info('circuit_breaker_half_open', elapsed=elapsed)

        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except Exception:
            self._on_failure()
            raise

    def _on_success(self) -> None:
        self._failure_count = 0
        self._is_open = False

    def _on_failure(self) -> None:
        self._failure_count += 1
        self._last_failure_time = datetime.now()
        if self._failure_count >= self._failure_threshold:
            self._is_open = True
            logger.warning(
                'circuit_breaker_open',
                failure_count=self._failure_count,
                recovery_timeout=self._recovery_timeout,
            )

    def reset(self) -> None:
        self._failure_count = 0
        self._is_open = False
        self._last_failure_time = None


# ═══════════════════════════════════════════════════════════
# HTTP Session Factory
# ═══════════════════════════════════════════════════════════


def create_session(
    pool_connections: int = 20,
    pool_maxsize: int = 20,
    max_retries: int = 3,
    connect_timeout: int = 5,
    read_timeout: int = 15,
) -> requests.Session:
    """HTTP 세션 생성 (🆕 재시도 비활성화)"""
    session = requests.Session()

    # 🆕 재시도 전략: 연결 실패만 재시도, 서버 오류는 재시도 안 함
    retry_strategy = Retry(
        total=max_retries,
        backoff_factor=1,
        # 🆕 status_forcelist 제거: 서버 오류(500, 502, 503, 504) 재시도 안 함
        status_forcelist=[429],  # Rate Limit만 재시도
        allowed_methods=['GET'],
        # 🆕 connect 에러만 재시도
        raise_on_status=False,
    )

    adapter = HTTPAdapter(
        pool_connections=pool_connections,
        pool_maxsize=pool_maxsize,
        max_retries=retry_strategy,
        pool_block=True,
    )

    session.mount('http://', adapter)
    session.mount('https://', adapter)

    return session


# ═══════════════════════════════════════════════════════════
# Abstract Base Adapter (🆕 Hard Timeout 추가)
# ═══════════════════════════════════════════════════════════


class BaseAdapter(ABC):
    source: str = ''
    cert_type: str = ''
    quota_provider: str | None = None
    pii_fields: set[str] = set()

    def __init__(self):
        self.settings = get_settings()
        self.circuit_breaker = CircuitBreaker(
            failure_threshold=self.settings.http_max_retries + 2,
            recovery_timeout=300,
        )
        self.session = create_session(
            connect_timeout=self.settings.http_timeout_connect,
            read_timeout=self.settings.http_timeout_read,
            max_retries=self.settings.http_max_retries,
        )
        self.raw_rows: list[dict[str, Any]] = []
        # 🆕 Hard Timeout을 위한 ThreadPoolExecutor (스레드 풀 크기 10)
        self._executor = ThreadPoolExecutor(max_workers=10)

    @abstractmethod
    def collect(self, province: str | None = None) -> list[CertRecord]:
        pass

    def check_quota(self) -> None:
        if self.quota_provider:
            from ..observability.quota import QuotaManager

            QuotaManager.get_instance().check_and_consume(self.quota_provider)

    def _safe_request(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> requests.Response | None:
        """
        안전한 HTTP 요청 (🆕 단일 timeout 사용)

        핵심 변경:
        - timeout=(connect, read) → timeout=total (단일 값)
        - 단일 timeout은 총 요청 시간에 적용 (connect + read)
        - 서버가 천천히 보내도 total 시간 후 무조건 타임아웃
        """
        hard_timeout = kwargs.pop('hard_timeout', None)
        if hard_timeout is None:
            hard_timeout = self.settings.http_timeout_read + 5

        # 🆕 단일 timeout 추출 (connect + read 합계)
        requests_timeout = kwargs.pop('timeout', None)
        if requests_timeout is None:
            # 🆕 단일 값 사용: connect + read = 5 + 10 = 15초
            requests_timeout = self.settings.http_timeout_connect + self.settings.http_timeout_read

        def do_request() -> requests.Response | None:
            try:
                future = self._executor.submit(
                    self.session.request,
                    method,
                    url,
                    timeout=requests_timeout,  # 🆕 단일 값
                    **kwargs,
                )
                return future.result(timeout=hard_timeout)
            except FuturesTimeoutError:
                logger.error(
                    'http_hard_timeout',
                    source=self.source,
                    url=url,
                    hard_timeout=hard_timeout,
                )
                return None
            except requests.exceptions.Timeout:
                logger.error(
                    'http_timeout',
                    source=self.source,
                    url=url,
                    timeout=requests_timeout,
                )
                return None

        try:
            return self.circuit_breaker.call(do_request)

        except CircuitOpenError as e:
            logger.error(
                'http_circuit_open',
                source=self.source,
                url=url,
                error=str(e),
            )
        except requests.exceptions.ConnectionError as e:
            logger.error(
                'http_connection_error',
                source=self.source,
                url=url,
                error=str(e),
            )
        except Exception as e:
            logger.error(
                'http_unknown_error',
                source=self.source,
                url=url,
                error=str(e),
                error_type=type(e).__name__,
            )

        return None

    def _register_raw_row(self, row: dict[str, Any]) -> None:
        self.raw_rows.append(row)

    def _clear_raw_rows(self) -> None:
        self.raw_rows.clear()
