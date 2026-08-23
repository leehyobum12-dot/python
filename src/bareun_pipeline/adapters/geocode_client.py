"""
Bareun Pipeline: Geocode Adapter Client
========================================
- 네이버 지오코딩 API 호출 전담 (I/O, Retry, Quota, Circuit Breaker)
- Silver 계층(silver/enrich/geocode.py)에서 의존성 주입받아 사용
- DNS Fallback: 네트워크 불안정 시 OS DNS 캐시 자동 플러시

위치: src/bareun_pipeline/adapters/geocode_client.py
"""
from __future__ import annotations

import asyncio
import platform
import subprocess
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from ..observability.logging import get_logger
from ..observability.quota import QuotaManager, QuotaExceededError
from ..settings import get_settings
from .base import CircuitBreaker

logger = get_logger(__name__)


@dataclass(frozen=True)
class GeocodeResult:
    """지오코딩 결과 불변 객체 (Silver 계층으로 전달용)"""
    province: str
    municipality: str
    emd: str
    road_name: str
    lot_num: str
    lat: float
    lng: float


def _flush_dns_cache() -> None:
    """DNS 실패 시 OS별 캐시 플러시 (Windows/Linux/macOS)"""
    system = platform.system()
    try:
        if system == "Windows":
            subprocess.run(
                ["ipconfig", "/flushdns"], 
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5
            )
        elif system == "Linux":
            subprocess.run(
                ["systemd-resolve", "--flush-caches"], 
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5
            )
        elif system == "Darwin":
            subprocess.run(
                ["dscacheutil", "-flushcache"], 
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5
            )
    except Exception:
        pass


class GeocodeAdapter:
    """네이버 지오코딩 어댑터"""
    NAVER_URL: str = "https://maps.apigw.ntruss.com/map-geocode/v2/geocode"

    def __init__(self, settings=None, quota_manager=None):
        self.settings = settings or get_settings()
        self.quota = quota_manager or QuotaManager.get_instance()
        self.circuit_breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=300)

    async def geocode(
        self,
        address: str,
        client: httpx.AsyncClient,
        max_retries: int = 3,
    ) -> Optional[GeocodeResult]:
        """주소 기반 지오코딩 수행 (지수 백오프 + DNS 재시도 적용)"""
        if self.circuit_breaker.is_open:
            return None

        headers = {
            "X-NCP-APIGW-API-KEY-ID": self.settings.naver_client_id,
            "X-NCP-APIGW-API-KEY": self.settings.naver_client_secret,
            "Referer": "http://localhost",
        }

        for attempt in range(max_retries):
            try:
                self.quota.check_and_consume("naver_geo")

                response = await client.get(
                    self.NAVER_URL,
                    params={"query": address},
                    headers=headers,
                    timeout=30,
                )

                if response.status_code == 200:
                    self.circuit_breaker._on_success()
                    return self._parse_response(response.json(), address)

                if response.status_code == 429:
                    self.circuit_breaker._on_failure()
                    wait_time = 2 ** attempt
                    logger.warning("geocode_rate_limit", attempt=attempt + 1, wait_seconds=wait_time)
                    await asyncio.sleep(wait_time)
                    continue

                self.circuit_breaker._on_failure()
                return None

            except (httpx.ConnectError, httpx.TimeoutException) as e:
                err_str = str(e).lower()
                if "getaddrinfo failed" in err_str or "nameresolutionerror" in err_str:
                    if attempt < max_retries - 1:
                        logger.warning("dns_failure_retry", attempt=attempt + 1)
                        _flush_dns_cache()
                        await asyncio.sleep(2 ** attempt)
                        continue
                self.circuit_breaker._on_failure()
                return None

            except QuotaExceededError:
                raise

            except Exception as e:
                logger.error("geocode_adapter_error", address=address, error=str(e))
                self.circuit_breaker._on_failure()
                return None

        return None

    def _parse_response(self, data: dict[str, Any], address: str) -> Optional[GeocodeResult]:
        """네이버 API 응답을 불변 데이터 클래스로 파싱"""
        if not data.get("addresses"):
            return None

        a = data["addresses"][0]
        province = municipality = emd = ""

        for el in a.get("addressElements", []):
            t = el.get("types", [])
            if "SIDO" in t:
                province = el.get("longName", "")
            elif "SIGUGUN" in t:
                municipality = el.get("longName", "")
            elif "DONGMYUN" in t:
                emd = el.get("longName", "")

        try:
            lat = float(a.get("y", 0))
            lng = float(a.get("x", 0))
        except (ValueError, TypeError):
            return None

        return GeocodeResult(
            province=province,
            municipality=municipality,
            emd=emd,
            road_name=a.get("roadAddress", address),
            lot_num=a.get("jibunAddress", ""),
            lat=lat,
            lng=lng,
        )