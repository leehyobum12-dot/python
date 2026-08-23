"""
Bareun Pipeline: Adapter Base (Slim)
=====================================
- HTTP 세션 관리, Circuit Breaker, BaseAdapter 추상 클래스
- 주소 유틸리티는 domain/address.py로 이관됨 (대안 B 확정)
- 하위 호환성을 위해 domain.address에서 re-export 유지

이관 내역 (→ src/bareun_pipeline/domain/address.py):
  - is_road_name, is_emd_token, extract_emd_from_addr
  - canon_province, match_prefixes, parse_addr_tokens
  - norm_name, norm_addr
  - PROVINCE_CANON, SIDO_ALIAS, ADDR_PATTERN
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Callable, Optional, Tuple, TypeVar

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..domain.record import CertRecord
from ..observability.logging import get_logger
from ..settings import get_settings

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

logger = get_logger(__name__)
T = TypeVar("T")

__all__ = [
    # Address utilities (re-exported for backward compatibility)
    "ADDR_PATTERN", "PROVINCE_CANON", "SIDO_ALIAS",
    "canon_province", "extract_emd_from_addr",
    "is_emd_token", "is_road_name", "match_prefixes",
    "norm_addr", "norm_name", "parse_addr_tokens",
    # Adapter infrastructure
    "CircuitOpenError", "CircuitBreaker", "create_session", "BaseAdapter",
]


# ═══════════════════════════════════════════════════════════
# Circuit Breaker
# ═══════════════════════════════════════════════════════════

class CircuitOpenError(Exception):
    """Circuit이 열려 있어 호출 불가할 때 발생"""
    pass


class CircuitBreaker:
    """
    Circuit Breaker 패턴 구현.
    연속 실패 시 API 호출을 일시 중단하여 리소스 낭비 및 과금 방지.
    """

    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 300):
        self._failure_count = 0
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._last_failure_time: Optional[datetime] = None
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
                        f"Circuit is open. Wait {self._recovery_timeout - elapsed:.0f}s"
                    )
                self._is_open = False
                self._failure_count = 0
                logger.info("circuit_breaker_half_open", elapsed=elapsed)

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
                "circuit_breaker_open",
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
    pool_maxsize: int = 50,
    max_retries: int = 3,
    connect_timeout: int = 5,
    read_timeout: int = 15,
) -> requests.Session:
    """HTTP 세션 생성 (재시도 정책 및 타임아웃 분리 적용)"""
    session = requests.Session()

    retry_strategy = Retry(
        total=max_retries,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )

    adapter = HTTPAdapter(
        pool_connections=pool_connections,
        pool_maxsize=pool_maxsize,
        max_retries=retry_strategy,
        pool_block=False,
    )

    session.mount("http://", adapter)
    session.mount("https://", adapter)

    return session


# ═══════════════════════════════════════════════════════════
# Abstract Base Adapter
# ═══════════════════════════════════════════════════════════

class BaseAdapter(ABC):
    """
    모든 공공데이터 수집 어댑터의 추상 베이스 클래스.
    서브클래스는 `source`, `cert_type`, `quota_provider`를 반드시 정의해야 합니다.
    """

    source: str = ""
    cert_type: str = ""
    quota_provider: Optional[str] = None
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

    @abstractmethod
    def collect(self, province: Optional[str] = None) -> list[CertRecord]:
        """데이터 수집 (서브클래스에서 구현)"""
        pass

    def check_quota(self) -> None:
        """쿼터 확인 (quota_provider가 설정된 경우)"""
        if self.quota_provider:
            from ..observability.quota import QuotaManager
            quota = QuotaManager.get_instance()
            quota.check_and_consume(self.quota_provider)

    def _safe_request(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> Optional[requests.Response]:
        """안전한 HTTP 요청 (Circuit Breaker + 타임아웃 래퍼)"""
        try:
            def do_request() -> requests.Response:
                return self.session.request(
                    method,
                    url,
                    timeout=(
                        self.settings.http_timeout_connect,
                        self.settings.http_timeout_read,
                    ),
                    **kwargs,
                )

            return self.circuit_breaker.call(do_request)

        except CircuitOpenError as e:
            logger.error(
                "http_circuit_open",
                source=self.source, url=url, error=str(e),
            )
        except requests.exceptions.Timeout as e:
            logger.error(
                "http_timeout",
                source=self.source, url=url, error=str(e),
            )
        except requests.exceptions.ConnectionError as e:
            logger.error(
                "http_connection_error",
                source=self.source, url=url, error=str(e),
            )
        except Exception as e:
            logger.error(
                "http_unknown_error",
                source=self.source, url=url,
                error=str(e), error_type=type(e).__name__,
            )

        return None

    def _register_raw_row(self, row: dict[str, Any]) -> None:
        """원본 데이터 등록 (Bronze 계층 아카이빙용)"""
        self.raw_rows.append(row)

    def _clear_raw_rows(self) -> None:
        """원본 데이터 초기화"""
        self.raw_rows.clear()