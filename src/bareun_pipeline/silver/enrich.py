"""
Silver Layer: 데이터 보강 (Enrichment)
========================================
- 지오코딩 (네이버 API, 캐시 우선)
- 좌표 이상치 탐지
- avoid_tags 자동 생성 (알레르기 기반)
- 가게 이름 검증 (네이버 + 카카오 맵 API 동시 사용)
- 메뉴 보강 (네이버 + 카카오, 허수 제거)
- 가게 정보 보강 (영업시간, 사진 등)
- DNS fallback (네트워크 불안정 대응)
"""
from __future__ import annotations

import asyncio
import json
import os
import platform
import re
import subprocess
import threading
import time
from datetime import datetime
from difflib import SequenceMatcher
from math import radians, cos, sin, asin, sqrt
from typing import List, Dict, Optional, Tuple

import httpx

from ..domain.record import CertRecord
from ..domain.avoid_tags import (
    generate_avoid_tags,
    assess_store_name_quality,
    needs_store_verification,
    build_search_query,
)
from ..observability.logging import get_logger
from ..observability.quota import QuotaManager, QuotaExceededError
from ..settings import get_settings
from ..adapters.base import CircuitBreaker, CircuitOpenError


logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════
# 좌표 이상치 탐지용 상수
# ═══════════════════════════════════════════════════════════

PROVINCE_CENTERS: Dict[str, Tuple[float, float]] = {
    '서울특별시': (37.5665, 126.9780),
    '부산광역시': (35.1796, 129.0756),
    '대구광역시': (35.8714, 128.6014),
    '인천광역시': (37.4563, 126.7052),
    '광주광역시': (35.1595, 126.8526),
    '대전광역시': (36.3504, 127.3845),
    '울산광역시': (35.5384, 129.3114),
    '세종특별자치시': (36.4800, 127.2890),
    '경기도': (37.4138, 127.5171),
    '강원특별자치도': (37.8228, 128.1555),
    '충청북도': (36.8001, 127.7200),
    '충청남도': (36.5184, 126.8000),
    '전북특별자치도': (35.7175, 127.1530),
    '전라남도': (35.1595, 126.8526),
    '경상북도': (36.4919, 128.8889),
    '경상남도': (35.4606, 128.2132),
    '제주특별자치도': (33.4996, 126.5312),
}

PROVINCE_RADIUS_M: Dict[str, int] = {
    '서울특별시': 20_000,
    '세종특별자치시': 20_000,
    '부산광역시': 40_000,
    '대구광역시': 40_000,
    '인천광역시': 50_000,
    '광주광역시': 30_000,
    '대전광역시': 30_000,
    '울산광역시': 40_000,
    '경기도': 80_000,
    '강원특별자치도': 150_000,
    '충청북도': 80_000,
    '충청남도': 80_000,
    '전북특별자치도': 80_000,
    '전라남도': 120_000,
    '경상북도': 150_000,
    '경상남도': 120_000,
    '제주특별자치도': 80_000,
}

MUNICIPALITY_EXCEPTIONS: Dict[Tuple[str, str], int] = {
    ('대구광역시', '군위군'): 60_000,
    ('경기도', '파주시'): 100_000,
    ('경기도', '연천군'): 120_000,
    ('인천광역시', '강화군'): 80_000,
    ('인천광역시', '옹진군'): 200_000,
    ('경상북도', '울릉군'): 250_000,
    ('경상북도', '울진군'): 180_000,
    ('경상남도', '거제시'): 140_000,
    ('경상남도', '남해군'): 140_000,
    ('경상남도', '통영시'): 140_000,
    ('전라남도', '신안군'): 160_000,
    ('전라남도', '진도군'): 160_000,
    ('전라남도', '완도군'): 160_000,
    ('강원특별자치도', '고성군'): 180_000,
    ('강원특별자치도', '인제군'): 160_000,
    ('충청남도', '금산군'): 100_000,
    ('충청남도', '서천군'): 100_000,
    ('경기도', '가평군'): 100_000,
    ('경기도', '양평군'): 90_000,
    ('경상북도', '봉화군'): 180_000,
    ('경상북도', '영덕군'): 170_000,
    ('전라남도', '여수시'): 150_000,
    ('전라남도', '고흥군'): 150_000,
}


# ═══════════════════════════════════════════════════════════
# 메뉴 검증 휴리스틱 (허수 제거)
# ═══════════════════════════════════════════════════════════

INVALID_MENU_PATTERNS = [
    r'^[0-9,]+원$',
    r'^\d+인분$',
    r'^[가-힣]{1}$',
    r'^세트$', r'^식사$', r'^음료$',
    r'^메뉴$', r'^요리$', r'^코스$',
    r'^기본$', r'^추가$',
    r'^(서비스|무료|제공)$',
]

STORE_NAME_IN_MENU_THRESHOLD = 0.7


def is_valid_menu_name(menu_name: str, store_name: Optional[str] = None) -> bool:
    """메뉴명 유효성 검증 (허수 제거)"""
    if not menu_name or not isinstance(menu_name, str):
        return False

    menu = menu_name.strip()

    if len(menu) < 2 or len(menu) > 50:
        return False

    for pattern in INVALID_MENU_PATTERNS:
        if re.match(pattern, menu):
            return False

    valid_chars = len(re.findall(r'[\uAC00-\uD7A3a-zA-Z0-9]', menu))
    if valid_chars / len(menu) < 0.5:
        return False

    if store_name and len(store_name) >= 2:
        ratio = SequenceMatcher(None, menu, store_name).ratio()
        if ratio >= STORE_NAME_IN_MENU_THRESHOLD:
            return False

    return True


# ═══════════════════════════════════════════════════════════
# 유틸리티 함수
# ═══════════════════════════════════════════════════════════

def haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """두 좌표 사이 거리 (미터)"""
    R = 6371000
    lat1, lng1, lat2, lng2 = map(radians, [lat1, lng1, lat2, lng2])
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlng / 2) ** 2
    return R * 2 * asin(sqrt(a))


def detect_coordinate_anomalies(
    records: List[CertRecord],
    cross_validate: bool = True,
) -> List[CertRecord]:
    """좌표 이상치 탐지 + 교차 검증"""
    anomalies = 0
    cross_validation_warnings = 0

    for record in records:
        if not record.lat or not record.lng or not record.province:
            continue

        center = PROVINCE_CENTERS.get(record.province)
        if center:
            key = (record.province, record.municipality or '')
            if key in MUNICIPALITY_EXCEPTIONS:
                max_radius = MUNICIPALITY_EXCEPTIONS[key]
            else:
                max_radius = PROVINCE_RADIUS_M.get(record.province, 100_000)

            dist = haversine(record.lat, record.lng, center[0], center[1])

            if dist > max_radius:
                anomalies += 1
                if anomalies <= 10:
                    logger.warning(
                        'coordinate_anomaly_province_center',
                        name=record.raw_name,
                        province=record.province,
                        distance_km=round(dist / 1000, 1),
                        max_km=max_radius // 1000,
                    )
                record.lat = None
                record.lng = None
                continue

        if cross_validate and record.raw and isinstance(record.raw, dict):
            general_lat = record.raw.get('general_lat')
            general_lng = record.raw.get('general_lng')
            
            if general_lat and general_lng:
                dist = haversine(record.lat, record.lng, general_lat, general_lng)
                
                if dist > 1000:
                    cross_validation_warnings += 1
                    if cross_validation_warnings <= 10:
                        logger.warning(
                            'coordinate_cross_validation_warning',
                            name=record.raw_name,
                            naver_lat=round(record.lat, 6),
                            naver_lng=round(record.lng, 6),
                            general_lat=round(general_lat, 6),
                            general_lng=round(general_lng, 6),
                            distance_m=round(dist, 1),
                        )

            tourist_lat = record.raw.get('tourist_lat')
            tourist_lng = record.raw.get('tourist_lng')
            
            if tourist_lat and tourist_lng:
                dist = haversine(record.lat, record.lng, tourist_lat, tourist_lng)
                
                if dist > 500:
                    cross_validation_warnings += 1
                    if cross_validation_warnings <= 10:
                        logger.warning(
                            'coordinate_cross_validation_warning_tourist',
                            name=record.raw_name,
                            naver_lat=round(record.lat, 6),
                            naver_lng=round(record.lng, 6),
                            tourist_lat=round(tourist_lat, 6),
                            tourist_lng=round(tourist_lng, 6),
                            distance_m=round(dist, 1),
                        )

    if anomalies > 0:
        logger.info('coordinate_anomalies_detected', count=anomalies)
    
    if cross_validation_warnings > 0:
        logger.info(
            'coordinate_cross_validation_warnings',
            count=cross_validation_warnings,
            threshold_general_m=1000,
            threshold_tourist_m=500,
        )

    return records


def _flush_dns_cache() -> None:
    """DNS 캐시 플러시 (Windows/Linux/macOS)"""
    system = platform.system()
    try:
        if system == 'Windows':
            subprocess.run(
                ['ipconfig', '/flushdns'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        elif system == 'Linux':
            subprocess.run(
                ['systemd-resolve', '--flush-caches'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        elif system == 'Darwin':
            subprocess.run(
                ['dscacheutil', '-flushcache'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
    except Exception:
        pass


def _retry_with_dns_fallback(func, *args, retries: int = 3, **kwargs):
    """DNS 실패 시 재시도 + DNS 캐시 플러시"""
    for attempt in range(retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            err_str = str(e).lower()
            if 'getaddrinfo failed' in err_str or 'nameresolutionerror' in err_str:
                if attempt < retries - 1:
                    logger.warning(
                        'dns_failure_retry',
                        attempt=attempt + 1,
                        retries=retries,
                    )
                    _flush_dns_cache()
                    time.sleep(2 ** attempt)
                    continue
            raise


# ═══════════════════════════════════════════════════════════
# 지오코딩 캐시
# ═══════════════════════════════════════════════════════════

class GeocodeCache:
    """지오코딩 캐시 (파일 기반)"""

    def __init__(self, cache_path: str):
        self._path = cache_path
        self._cache: Dict[str, Optional[dict]] = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if os.path.exists(self._path):
            try:
                with open(self._path, encoding='utf-8') as f:
                    self._cache = json.load(f)
                logger.info('geocode_cache_loaded', count=len(self._cache))
            except Exception as e:
                logger.warning('geocode_cache_load_failed', path=self._path, error=str(e))
                self._cache = {}

    def save(self) -> None:
        """캐시를 파일에 저장 (원자적 쓰기)"""
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with self._lock:
                temp_path = self._path + '.tmp'
                with open(temp_path, 'w', encoding='utf-8') as f:
                    json.dump(self._cache, f, ensure_ascii=False)
                if os.path.exists(self._path):
                    os.replace(temp_path, self._path)
                else:
                    os.rename(temp_path, self._path)
        except Exception as e:
            logger.warning('geocode_cache_save_failed', path=self._path, error=str(e))

    def get(self, address: str) -> Optional[dict]:
        with self._lock:
            return self._cache.get(address)

    def set(self, address: str, result: Optional[dict]) -> None:
        with self._lock:
            self._cache[address] = result

    def __len__(self) -> int:
        return len(self._cache)


# ═══════════════════════════════════════════════════════════
# 지오코더
# ═══════════════════════════════════════════════════════════

class Geocoder:
    """네이버 지오코딩 API 기반 좌표 보강"""

    NAVER_URL = 'https://maps.apigw.ntruss.com/map-geocode/v2/geocode'

    def __init__(self):
        self.settings = get_settings()
        self.quota = QuotaManager.get_instance()
        self.cache = GeocodeCache(
            os.path.join(self.settings.cache_dir, 'geocode', 'geocode_cache.json')
        )
        self.circuit_breaker = CircuitBreaker(
            failure_threshold=5,
            recovery_timeout=300,
        )

    async def geocode_batch(
        self,
        records: List[CertRecord],
        max_concurrency: int = 3,  # 🆕 5 → 3 (속도 제한 방지)
        force: bool = False,
    ) -> List[CertRecord]:
        """
        배치 지오코딩 (서킷 브레이커 + 재시도 적용)
        
        Args:
            records: 대상 레코드
            max_concurrency: 동시 요청 수 (3 권장)
            force: True면 기존 좌표가 있어도 네이버 지오코딩 수행
        
        Returns:
            지오코딩된 레코드 목록
        """
        if force:
            targets = [r for r in records if r.raw_address]
        else:
            targets = [r for r in records if not r.has_coordinate()]

        if not targets:
            logger.info('geocode_skip_no_targets')
            return records

        logger.info(
            'geocode_start',
            targets=len(targets),
            total=len(records),
            force=force,
        )

        cid = self.settings.naver_client_id
        csec = self.settings.naver_client_secret

        if not cid or not csec:
            logger.warning('geocode_skip_no_api_key')
            return records

        api_calls = 0
        cache_hits = 0
        retry_count = 0

        async with httpx.AsyncClient(timeout=30) as client:  # 🆕 15 → 30초
            sem = asyncio.Semaphore(max_concurrency)

            async def geocode_one(record: CertRecord) -> None:
                nonlocal api_calls, cache_hits, retry_count

                address = record.raw_address.strip()
                if not address:
                    return

                cached = self.cache.get(address)
                if cached is not None:
                    cache_hits += 1
                    self._apply_hit(record, cached)
                    return

                if self.circuit_breaker.is_open:
                    logger.warning('geocode_circuit_open_skipping', address=address)
                    return

                async with sem:
                    for attempt in range(3):  # 🆕 3회 재시도
                        try:
                            self.quota.check_and_consume('naver_geo')
                            api_calls += 1

                            headers = {
                                'X-NCP-APIGW-API-KEY-ID': cid,
                                'X-NCP-APIGW-API-KEY': csec,
                                'Referer': 'http://localhost',
                            }

                            response = await client.get(
                                self.NAVER_URL,
                                params={'query': address},
                                headers=headers,
                                timeout=30,  # 🆕 15 → 30초
                            )

                            if response.status_code == 200:
                                data = response.json()
                                hit = self._parse_naver_payload(data, address)
                                self.cache.set(address, hit)
                                self._apply_hit(record, hit)
                                self.circuit_breaker._on_success()
                                break  # 성공 시 루프 종료
                            
                            elif response.status_code == 429:  # 🆕 Rate limit
                                retry_count += 1
                                wait_time = 2 ** attempt
                                logger.warning(
                                    'geocode_rate_limit',
                                    attempt=attempt + 1,
                                    wait_seconds=wait_time,
                                    address=address,
                                )
                                await asyncio.sleep(wait_time)
                                continue
                            
                            else:
                                self.cache.set(address, None)
                                self.circuit_breaker._on_failure()
                                break

                            await asyncio.sleep(0.1)  # 🆕 0.05 → 0.1초

                        except (httpx.TimeoutException, httpx.ConnectError) as e:
                            retry_count += 1
                            if attempt < 2:
                                wait_time = 2 ** (attempt + 1)
                                logger.warning(
                                    'geocode_timeout_retry',
                                    attempt=attempt + 1,
                                    wait_seconds=wait_time,
                                    address=address,
                                )
                                await asyncio.sleep(wait_time)
                                continue
                            else:
                                logger.error('geocode_timeout_final', address=address)
                                self.cache.set(address, None)
                                self.circuit_breaker._on_failure()
                                break

                        except QuotaExceededError:
                            logger.error('geocode_quota_exceeded')
                            self.cache.save()
                            raise

                        except Exception as e:
                            logger.error('geocode_error', address=address, error=str(e))
                            self.cache.set(address, None)
                            self.circuit_breaker._on_failure()
                            break

            chunk_size = 200
            for i in range(0, len(targets), chunk_size):
                chunk = targets[i:i + chunk_size]
                try:
                    await asyncio.gather(*[geocode_one(r) for r in chunk])
                except QuotaExceededError:
                    logger.error('geocode_batch_quota_exceeded')
                    break

                self.cache.save()
                logger.info(
                    'geocode_progress',
                    processed=min(i + chunk_size, len(targets)),
                    total=len(targets),
                    api_calls=api_calls,
                    cache_hits=cache_hits,
                    retries=retry_count,
                )

        self.cache.save()
        logger.info(
            'geocode_complete',
            api_calls=api_calls,
            cache_hits=cache_hits,
            retries=retry_count,
        )

        return records

    def _parse_naver_payload(self, data: dict, address: str) -> Optional[dict]:
        """네이버 API 응답 파싱"""
        if not data.get('addresses'):
            return None

        a = data['addresses'][0]
        province = municipality = emd = ''

        for el in a.get('addressElements', []):
            t = el.get('types', [])
            if 'SIDO' in t:
                province = el.get('longName', '')
            elif 'SIGUGUN' in t:
                municipality = el.get('longName', '')
            elif 'DONGMYUN' in t:
                emd = el.get('longName', '')

        try:
            lat = float(a.get('y', 0))
            lng = float(a.get('x', 0))
        except (ValueError, TypeError):
            return None

        return {
            'province': province,
            'municipality': municipality,
            'emd': emd,
            'road_name': a.get('roadAddress', address),
            'lot_num': a.get('jibunAddress', ''),
            'lat': lat,
            'lng': lng,
        }

    def _apply_hit(self, record: CertRecord, hit: Optional[dict]) -> None:
        """지오코딩 결과 적용 (네이버 우선)"""
        if not hit:
            return

        record.province = hit.get('province') or record.province
        record.municipality = hit.get('municipality') or record.municipality
        record.emd = hit.get('emd') or record.emd
        record.road_name = hit.get('road_name') or record.road_name
        record.lot_num = hit.get('lot_num') or record.lot_num
        
        record.lat = hit.get('lat')
        record.lng = hit.get('lng')

# ═══════════════════════════════════════════════════════════
# 나머지 코드는 기존과 동일 (StoreNameVerifier, MenuEnricher, StoreInfoEnricher, SilverEnricher)
# ═══════════════════════════════════════════════════════════

class StoreNameVerifier:
    """가게 이름 검증기 (네이버 + 카카오 맵 API 동시 사용)"""

    NAVER_SEARCH_URL = 'https://openapi.naver.com/v1/search/local.json'
    KAKAO_SEARCH_URL = 'https://dapi.kakao.com/v2/local/search/keyword.json'

    def __init__(self):
        self.settings = get_settings()
        self.cache: Dict[str, Optional[str]] = {}
        self._cache_lock = threading.Lock()
        self._load_cache()

    def _load_cache(self) -> None:
        cache_path = os.path.join(self.settings.cache_dir, 'store_verify', 'store_verify_cache.json')
        if os.path.exists(cache_path):
            try:
                with open(cache_path, encoding='utf-8') as f:
                    self.cache = json.load(f)
                logger.info('store_verify_cache_loaded', count=len(self.cache))
            except Exception:
                self.cache = {}

    def _save_cache(self) -> None:
        cache_path = os.path.join(self.settings.cache_dir, 'store_verify', 'store_verify_cache.json')
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with self._cache_lock:
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(self.cache, f, ensure_ascii=False)

    def _get_cache(self, query: str) -> Optional[str]:
        with self._cache_lock:
            return self.cache.get(query)

    def _set_cache(self, query: str, result: Optional[str]) -> None:
        with self._cache_lock:
            self.cache[query] = result

    async def verify_with_naver(self, client: httpx.AsyncClient, query: str) -> Optional[str]:
        cid = self.settings.naver_client_id
        csec = self.settings.naver_client_secret

        if not cid or not csec:
            return None

        try:
            response = await client.get(
                self.NAVER_SEARCH_URL,
                headers={'X-Naver-Client-Id': cid, 'X-Naver-Client-Secret': csec},
                params={'query': query, 'display': 3, 'start': 1},
                timeout=10.0,
            )

            if response.status_code == 200:
                data = response.json()
                items = data.get('items', [])

                for item in items:
                    title = re.sub(r'<[^>]+>', '', item.get('title', '')).strip()
                    if title and len(title) > 2:
                        return title

        except Exception as e:
            logger.error('verify_naver_error', query=query, error=str(e))

        return None

    async def verify_with_kakao(self, client: httpx.AsyncClient, query: str) -> Optional[str]:
        kakao_key = self.settings.kakao_rest_api_key

        if not kakao_key:
            return None

        try:
            response = await client.get(
                self.KAKAO_SEARCH_URL,
                headers={'Authorization': f'KakaoAK {kakao_key}'},
                params={'query': query, 'size': 3, 'page': 1},
                timeout=10.0,
            )

            if response.status_code == 200:
                data = response.json()
                documents = data.get('documents', [])

                for doc in documents:
                    place_name = doc.get('place_name', '').strip()
                    if place_name and len(place_name) > 2:
                        return place_name

        except Exception as e:
            logger.error('verify_kakao_error', query=query, error=str(e))

        return None

    async def verify_store(self, client: httpx.AsyncClient, query: str) -> Optional[str]:
        cached = self._get_cache(query)
        if cached is not None:
            return cached if cached else None

        naver_task = asyncio.create_task(self.verify_with_naver(client, query))
        kakao_task = asyncio.create_task(self.verify_with_kakao(client, query))

        result = None
        done, pending = await asyncio.wait(
            [naver_task, kakao_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in done:
            try:
                task_result = task.result()
                if task_result:
                    result = task_result
                    break
            except Exception:
                pass

        if result:
            for task in pending:
                task.cancel()
        else:
            for task in pending:
                try:
                    task_result = await task
                    if task_result:
                        result = task_result
                        break
                except (asyncio.CancelledError, Exception):
                    pass

        self._set_cache(query, result)
        return result

    async def verify_batch(self, records: List[CertRecord], max_concurrency: int = 3) -> List[CertRecord]:
        needs_verification = [r for r in records if needs_store_verification(r.store_name_quality or 'valid')]

        if not needs_verification:
            logger.info('verify_store_names_skip_none_needed')
            return records

        logger.info(
            'verify_store_names_start',
            count=len(needs_verification),
            naver_available=bool(self.settings.naver_client_id),
            kakao_available=bool(self.settings.kakao_rest_api_key),
        )

        verified_count = 0
        failed_count = 0

        async with httpx.AsyncClient() as client:
            sem = asyncio.Semaphore(max_concurrency)

            async def verify_one(record: CertRecord) -> None:
                nonlocal verified_count, failed_count

                async with sem:
                    query = build_search_query(
                        record.province,
                        record.municipality,
                        record.emd,
                        record.road_name or record.raw_address,
                        record.biz_type,
                        record.raw_name,
                    )

                    verified_name = await self.verify_store(client, query)

                    if verified_name:
                        old_name = record.raw_name
                        record.raw_name = verified_name
                        record.store_name_quality = 'verified'
                        verified_count += 1

                        logger.info('verify_store_name_updated', old_name=old_name, new_name=verified_name)
                    else:
                        failed_count += 1
                        logger.warning('verify_store_name_not_found', store_name=record.raw_name)

                    await asyncio.sleep(0.3)

            chunk_size = 20
            for i in range(0, len(needs_verification), chunk_size):
                chunk = needs_verification[i:i + chunk_size]
                await asyncio.gather(*[verify_one(r) for r in chunk])

                logger.info(
                    'verify_store_names_progress',
                    processed=min(i + chunk_size, len(needs_verification)),
                    total=len(needs_verification),
                    verified=verified_count,
                    failed=failed_count,
                )

        self._save_cache()

        logger.info('verify_store_names_complete', verified=verified_count, failed=failed_count, total=len(needs_verification))

        return records


class MenuEnricher:
    """메뉴 보강기 (네이버 + 카카오, 허수 제거)"""

    NAVER_LOCAL_URL = 'https://openapi.naver.com/v1/search/local.json'
    KAKAO_KEYWORD_URL = 'https://dapi.kakao.com/v2/local/search/keyword.json'

    def __init__(self):
        self.settings = get_settings()
        self.cache: Dict[str, dict] = {}
        self._cache_lock = threading.Lock()
        self._load_cache()

    def _load_cache(self) -> None:
        cache_path = os.path.join(self.settings.cache_dir, 'menu', 'menu_cache.json')
        if os.path.exists(cache_path):
            try:
                with open(cache_path, encoding='utf-8') as f:
                    self.cache = json.load(f)
                logger.info('menu_cache_loaded', count=len(self.cache))
            except Exception:
                self.cache = {}

    def _save_cache(self) -> None:
        cache_path = os.path.join(self.settings.cache_dir, 'menu', 'menu_cache.json')
        try:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            with self._cache_lock:
                with open(cache_path, 'w', encoding='utf-8') as f:
                    json.dump(self.cache, f, ensure_ascii=False)
        except Exception as e:
            logger.warning('menu_cache_save_failed', error=str(e))

    def _get_cache(self, key: str) -> Optional[dict]:
        with self._cache_lock:
            return self.cache.get(key)

    def _set_cache(self, key: str, value: dict) -> None:
        with self._cache_lock:
            self.cache[key] = value

    def _build_query(self, record: CertRecord) -> str:
        parts = []
        if record.road_name:
            parts.append(record.road_name)
        elif record.lot_num:
            parts.append(record.lot_num)
        elif record.raw_address:
            parts.append(record.raw_address)

        if record.biz_type:
            parts.append(record.biz_type)

        if (record.raw_name and len(record.raw_name) > 2
                and not any(re.match(p, record.raw_name) for p in INVALID_MENU_PATTERNS)):
            parts.append(record.raw_name)

        return ' '.join(parts[:3])

    async def _fetch_naver_menu(self, client: httpx.AsyncClient, query: str) -> dict:
        cid = self.settings.naver_client_id
        csec = self.settings.naver_client_secret

        if not cid or not csec:
            return {'menus': [], 'source': None}

        try:
            response = await client.get(
                self.NAVER_LOCAL_URL,
                headers={'X-Naver-Client-Id': cid, 'X-Naver-Client-Secret': csec},
                params={'query': query, 'display': 5, 'start': 1},
                timeout=10.0,
            )

            if response.status_code != 200:
                return {'menus': [], 'source': None}

            data = response.json()
            items = data.get('items', [])

            menus = []
            source = None

            for item in items:
                title = re.sub(r'<[^>]+>', '', item.get('title', '')).strip()
                description = item.get('description', '').strip()

                if description:
                    candidates = re.split(r'[,;|/\n·]', description)
                    for candidate in candidates:
                        candidate = candidate.strip()
                        if candidate and 2 <= len(candidate) <= 50:
                            menus.append(candidate)

                if not source and title and len(title) > 2:
                    source = 'naver_local'

            return {'menus': menus[:10], 'source': source}

        except Exception as e:
            logger.debug('menu_naver_error', error=str(e))
            return {'menus': [], 'source': None}

    async def _fetch_kakao_menu(self, client: httpx.AsyncClient, query: str) -> dict:
        kakao_key = self.settings.kakao_rest_api_key

        if not kakao_key:
            return {'menus': [], 'source': None}

        try:
            response = await client.get(
                self.KAKAO_KEYWORD_URL,
                headers={'Authorization': f'KakaoAK {kakao_key}'},
                params={'query': query, 'size': 5, 'page': 1},
                timeout=10.0,
            )

            if response.status_code != 200:
                return {'menus': [], 'source': None}

            data = response.json()
            documents = data.get('documents', [])

            menus = []
            source = None

            for doc in documents:
                place_name = doc.get('place_name', '').strip()
                if not source and place_name and len(place_name) > 2:
                    source = 'kakao_local'

            return {'menus': menus, 'source': source}

        except Exception as e:
            logger.debug('menu_kakao_error', error=str(e))
            return {'menus': [], 'source': None}

    async def enrich_batch(self, records: List[CertRecord], max_concurrency: int = 3) -> List[CertRecord]:
        """메뉴 보강 배치 처리"""
        needs_enrichment = [r for r in records if not r.main_menu or not r.menus]

        if not needs_enrichment:
            logger.info('menu_enrich_skip_all_have_menus')
            return records

        # 🆕 TouristInfo는 이미 메뉴 파싱됨
        tourist_with_menu = sum(
            1 for r in records 
            if r.source == 'tourist_info' and r.main_menu
        )
        
        logger.info(
            'menu_enrich_start',
            count=len(needs_enrichment),
            tourist_with_menu=tourist_with_menu,
            naver=bool(self.settings.naver_client_id),
            kakao=bool(self.settings.kakao_rest_api_key),
        )

        enriched_count = 0

        async with httpx.AsyncClient() as client:
            sem = asyncio.Semaphore(max_concurrency)

            async def enrich_one(record: CertRecord) -> None:
                nonlocal enriched_count

                async with sem:
                    query = self._build_query(record)
                    cache_key = f"{query}|{record.raw_name or ''}"

                    cached = self._get_cache(cache_key)
                    if cached is not None:
                        self._apply_menus(record, cached)
                        if cached.get('menus'):
                            enriched_count += 1
                        await asyncio.sleep(0.05)
                        return

                    naver_task = asyncio.create_task(self._fetch_naver_menu(client, query))
                    kakao_task = asyncio.create_task(self._fetch_kakao_menu(client, query))

                    done, pending = await asyncio.wait(
                        [naver_task, kakao_task],
                        return_when=asyncio.FIRST_COMPLETED,
                    )

                    result = {'menus': [], 'source': None}
                    for task in done:
                        try:
                            task_result = task.result()
                            if task_result.get('menus'):
                                result = task_result
                                break
                        except Exception:
                            pass

                    if not result['menus']:
                        for task in pending:
                            try:
                                task_result = await task
                                if task_result.get('menus'):
                                    result = task_result
                                    break
                            except (asyncio.CancelledError, Exception):
                                pass
                    else:
                        for task in pending:
                            task.cancel()

                    self._set_cache(cache_key, result)
                    self._apply_menus(record, result)
                    if result.get('menus'):
                        enriched_count += 1

                    await asyncio.sleep(0.2)

            chunk_size = 20
            for i in range(0, len(needs_enrichment), chunk_size):
                chunk = needs_enrichment[i:i + chunk_size]
                await asyncio.gather(*[enrich_one(r) for r in chunk])

                logger.info(
                    'menu_enrich_progress',
                    processed=min(i + chunk_size, len(needs_enrichment)),
                    total=len(needs_enrichment),
                    enriched=enriched_count,
                )

        self._save_cache()

        logger.info(
            'menu_enrich_complete',
            enriched=enriched_count,
            total=len(needs_enrichment),
            note='네이버/카카오 로컬 API는 메뉴 정보를 제공하지 않습니다. TouristInfo 상세 조회를 활용하세요.',
        )

        return records

    def _apply_menus(self, record: CertRecord, data: dict) -> None:
        menus = data.get('menus', [])
        source = data.get('source')

        if not menus:
            return

        valid_menus = [m for m in menus if is_valid_menu_name(m, record.raw_name)]

        if not valid_menus:
            return

        if record.main_menu:
            all_menus = sorted(set([record.main_menu] + valid_menus))
            record.menus = all_menus
            if not record.menus_source:
                record.menus_source = source
        else:
            record.main_menu = valid_menus[0]
            record.main_menu_source = source
            record.menus = sorted(set(valid_menus))
            record.menus_source = source


class StoreInfoEnricher:
    """가게 정보 보강기 (영업시간, 사진 등)"""

    NAVER_LOCAL_URL = 'https://openapi.naver.com/v1/search/local.json'
    KAKAO_KEYWORD_URL = 'https://dapi.kakao.com/v2/local/search/keyword.json'

    def __init__(self):
        self.settings = get_settings()
        self.cache: Dict[str, dict] = {}
        self._cache_lock = threading.Lock()
        self._load_cache()

    def _load_cache(self) -> None:
        cache_path = os.path.join(self.settings.cache_dir, 'store_info', 'store_info_cache.json')
        if os.path.exists(cache_path):
            try:
                with open(cache_path, encoding='utf-8') as f:
                    self.cache = json.load(f)
                logger.info('store_info_cache_loaded', count=len(self.cache))
            except Exception:
                self.cache = {}

    def _save_cache(self) -> None:
        cache_path = os.path.join(self.settings.cache_dir, 'store_info', 'store_info_cache.json')
        try:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            with self._cache_lock:
                with open(cache_path, 'w', encoding='utf-8') as f:
                    json.dump(self.cache, f, ensure_ascii=False)
        except Exception as e:
            logger.warning('store_info_cache_save_failed', error=str(e))

    def _get_cache(self, key: str) -> Optional[dict]:
        with self._cache_lock:
            return self.cache.get(key)

    def _set_cache(self, key: str, value: dict) -> None:
        with self._cache_lock:
            self.cache[key] = value

    def _build_query(self, record: CertRecord) -> str:
        parts = []
        if record.road_name:
            parts.append(record.road_name)
        elif record.raw_address:
            parts.append(record.raw_address)

        if record.raw_name and len(record.raw_name) > 2:
            parts.append(record.raw_name)

        return ' '.join(parts[:2])

    async def _fetch_naver_info(self, client: httpx.AsyncClient, query: str) -> dict:
        cid = self.settings.naver_client_id
        csec = self.settings.naver_client_secret

        if not cid or not csec:
            return {}

        try:
            response = await client.get(
                self.NAVER_LOCAL_URL,
                headers={'X-Naver-Client-Id': cid, 'X-Naver-Client-Secret': csec},
                params={'query': query, 'display': 3, 'start': 1},
                timeout=10.0,
            )

            if response.status_code != 200:
                return {}

            data = response.json()
            items = data.get('items', [])

            if not items:
                return {}

            item = items[0]

            title = re.sub(r'<[^>]+>', '', item.get('title', '')).strip()
            link = item.get('link', '')
            description = item.get('description', '').strip()
            telephone = item.get('telephone', '').strip()
            address = item.get('address', '').strip()
            road_address = item.get('roadAddress', '').strip()

            mapx = item.get('mapx')
            mapy = item.get('mapy')

            photos = []
            if mapx and mapy:
                static_map_url = (
                    f"https://naveropenapi.apigw.ntruss.com/map-static/v2/map-img"
                    f"?w=400&h=400&center={mapx},{mapy}&level=16"
                    f"&X-NCP-APIGW-API-KEY-ID={cid}"
                )
                photos.append({'url': static_map_url, 'type': 'map_thumbnail'})

            return {
                'title': title,
                'link': link,
                'description': description,
                'telephone': telephone,
                'address': address,
                'road_address': road_address,
                'photos': photos,
                'source': 'naver_local',
            }

        except Exception as e:
            logger.debug('store_info_naver_error', error=str(e))
            return {}

    async def _fetch_kakao_info(self, client: httpx.AsyncClient, query: str) -> dict:
        kakao_key = self.settings.kakao_rest_api_key

        if not kakao_key:
            return {}

        try:
            response = await client.get(
                self.KAKAO_KEYWORD_URL,
                headers={'Authorization': f'KakaoAK {kakao_key}'},
                params={'query': query, 'size': 3, 'page': 1},
                timeout=10.0,
            )

            if response.status_code != 200:
                return {}

            data = response.json()
            documents = data.get('documents', [])

            if not documents:
                return {}

            doc = documents[0]

            place_name = doc.get('place_name', '').strip()
            place_url = doc.get('place_url', '')
            phone = doc.get('phone', '').strip()
            address_name = doc.get('address_name', '').strip()
            road_address_name = doc.get('road_address_name', '').strip()
            category_name = doc.get('category_name', '').strip()
            x = doc.get('x')
            y = doc.get('y')

            photos = []
            if x and y:
                static_map_url = (
                    f"https://dapi.kakao.com/v2/maps/static/v2"
                    f"?center={x},{y}&level=3&width=400&height=400"
                )
                photos.append({'url': static_map_url, 'type': 'map_thumbnail'})

            return {
                'title': place_name,
                'link': place_url,
                'telephone': phone,
                'address': address_name,
                'road_address': road_address_name,
                'category': category_name,
                'photos': photos,
                'source': 'kakao_local',
            }

        except Exception as e:
            logger.debug('store_info_kakao_error', error=str(e))
            return {}

    async def enrich_batch(self, records: List[CertRecord], max_concurrency: int = 3) -> List[CertRecord]:
        logger.info('store_info_enrich_start', count=len(records))

        enriched_count = 0

        async with httpx.AsyncClient(timeout=30) as client:
            sem = asyncio.Semaphore(max_concurrency)

            async def enrich_one(record: CertRecord) -> None:
                nonlocal enriched_count

                async with sem:
                    query = self._build_query(record)
                    cache_key = f"{record.raw_address or ''}|{record.raw_name or ''}"

                    cached = self._get_cache(cache_key)
                    if cached is not None:
                        self._apply_info(record, cached)
                        if cached:
                            enriched_count += 1
                        await asyncio.sleep(0.05)
                        return

                    naver_task = asyncio.create_task(self._fetch_naver_info(client, query))
                    kakao_task = asyncio.create_task(self._fetch_kakao_info(client, query))

                    done, pending = await asyncio.wait(
                        [naver_task, kakao_task],
                        return_when=asyncio.FIRST_COMPLETED,
                    )

                    result = {}
                    for task in done:
                        try:
                            task_result = task.result()
                            if task_result:
                                result = task_result
                                break
                        except Exception:
                            pass

                    if not result:
                        for task in pending:
                            try:
                                task_result = await task
                                if task_result:
                                    result = task_result
                                    break
                            except (asyncio.CancelledError, Exception):
                                pass
                    else:
                        for task in pending:
                            task.cancel()

                    self._set_cache(cache_key, result)
                    self._apply_info(record, result)
                    if result:
                        enriched_count += 1

                    await asyncio.sleep(0.2)

            chunk_size = 20
            for i in range(0, len(records), chunk_size):
                chunk = records[i:i + chunk_size]
                await asyncio.gather(*[enrich_one(r) for r in chunk])

                logger.info(
                    'store_info_enrich_progress',
                    processed=min(i + chunk_size, len(records)),
                    total=len(records),
                    enriched=enriched_count,
                )

        self._save_cache()

        logger.info('store_info_enrich_complete', enriched=enriched_count, total=len(records))

        return records

    def _apply_info(self, record: CertRecord, data: dict) -> None:
        if not data:
            return

        if not record.phone and data.get('telephone'):
            record.phone = data['telephone']

        if data.get('road_address') and not record.road_name:
            record.road_name = data['road_address']
        if data.get('address') and not record.lot_num:
            record.lot_num = data['address']

        if record.raw is None:
            record.raw = {}
        if isinstance(record.raw, dict):
            record.raw['external_info'] = {
                'link': data.get('link'),
                'photos': data.get('photos', []),
                'source': data.get('source'),
                'fetched_at': datetime.now().isoformat(),
            }


class SilverEnricher:
    """Silver Layer 통합 보강기"""

    def __init__(self):
        self.geocoder = Geocoder()
        self.store_verifier = StoreNameVerifier()
        self.menu_enricher = MenuEnricher()
        self.store_info_enricher = StoreInfoEnricher()

    async def enrich(
        self,
        records: List[CertRecord],
        verify_store_names: bool = True,
        enrich_menus: bool = True,
        enrich_store_info: bool = True,
        force_geocoding: bool = False,
    ) -> List[CertRecord]:
        logger.info('silver_enrich_start', count=len(records))

        records = await self.geocoder.geocode_batch(records, force=force_geocoding)
        records = detect_coordinate_anomalies(records)

        for record in records:
            if record.allergen_tags:
                record.avoid_tags = generate_avoid_tags(
                    allergen_tags=record.allergen_tags,
                    biz_type=record.biz_type,
                    main_menu=record.main_menu,
                    menus=record.menus,
                )

        for record in records:
            record.store_name_quality = assess_store_name_quality(
                record.raw_name,
                record.province,
                record.municipality,
            )

        if verify_store_names:
            needs_verify_count = sum(
                1 for r in records
                if needs_store_verification(r.store_name_quality or 'valid')
            )
            if needs_verify_count > 0:
                records = await self.store_verifier.verify_batch(records)

        if enrich_menus:
            records = await self.menu_enricher.enrich_batch(records)

        if enrich_store_info:
            records = await self.store_info_enricher.enrich_batch(records)

        logger.info('silver_enrich_complete', count=len(records))

        return records


async def enrich_coordinates(records: List[CertRecord]) -> List[CertRecord]:
    enricher = SilverEnricher()
    return await enricher.enrich(records, verify_store_names=False, enrich_menus=False, enrich_store_info=False)


async def verify_store_names(records: List[CertRecord]) -> List[CertRecord]:
    enricher = SilverEnricher()
    return await enricher.enrich(records, verify_store_names=True, enrich_menus=False, enrich_store_info=False)