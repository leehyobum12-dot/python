"""
Silver Layer: 데이터 보강 (Enrichment)
========================================
- 지오코딩 (네이버 API, 캐시 우선)
- 좌표 이상치 탐지
- avoid_tags 자동 생성 (알레르기 기반)
- 가게 이름 검증 (네이버 + 카카오 맵 API 동시 사용)
- DNS fallback (네트워크 불안정 대응)

기존 참고:
- enrich/geocoder.py의 지오코딩 로직
- enrich/geocoder.py의 DNS fallback
- enrich/geocoder.py의 좌표 이상치 탐지

보존 사항:
- 지오코딩 캐시
- DNS fallback (캐시 플러시 후 재시도)
- 좌표 이상치 탐지 (시도별 반경)
- 시군구 예외 처리

개선 사항:
- QuotaManager 연동
- GeocodeCache 클래스로 캡슐화
- 구조화 로깅
- 네이버/카카오 동시 검증 (속도 + 정확도 향상)
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
# 좌표 이상치 탐지용 상수 (기존 enrich/geocoder.py에서 이관)
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

# 시군구별 예외 (실제 지역이지만 중심에서 먼 곳)
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


def haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """두 좌표 사이 거리 (미터)"""
    R = 6371000
    lat1, lng1, lat2, lng2 = map(radians, [lat1, lng1, lat2, lng2])
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlng / 2) ** 2
    return R * 2 * asin(sqrt(a))


def detect_coordinate_anomalies(records: List[CertRecord]) -> List[CertRecord]:
    """
    좌표 이상치 탐지

    기존 enrich/geocoder.py의 detect_coordinate_anomalies() 함수 보존

    Args:
        records: 검사할 레코드 목록

    Returns:
        이상치 제거된 레코드 목록
    """
    anomalies = 0

    for record in records:
        if not record.lat or not record.lng or not record.province:
            continue

        center = PROVINCE_CENTERS.get(record.province)
        if not center:
            continue

        # 시군구 예외 체크
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
                    'coordinate_anomaly',
                    name=record.raw_name,
                    province=record.province,
                    municipality=record.municipality,
                    distance_km=round(dist / 1000, 1),
                    max_km=max_radius // 1000,
                )
            record.lat = None
            record.lng = None

    if anomalies > 0:
        logger.info('coordinate_anomalies_detected', count=anomalies)

    return records


# ═══════════════════════════════════════════════════════════
# DNS Fallback 유틸리티 (기존 enrich/geocoder.py에서 이관)
# ═══════════════════════════════════════════════════════════

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
        pass  # 실패해도 무시


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
                logger.info(
                    'geocode_cache_loaded',
                    count=len(self._cache),
                )
            except Exception as e:
                logger.warning(
                    'geocode_cache_load_failed',
                    path=self._path,
                    error=str(e),
                )
                self._cache = {}

    def save(self) -> None:
        """캐시를 파일에 저장 (원자적 쓰기)"""
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with self._lock:
                # 임시 파일로 먼저 쓰고 교체 (원자적 쓰기)
                temp_path = self._path + '.tmp'
                with open(temp_path, 'w', encoding='utf-8') as f:
                    json.dump(self._cache, f, ensure_ascii=False)
                # 성공 시 원본 파일 교체
                if os.path.exists(self._path):
                    os.replace(temp_path, self._path)
                else:
                    os.rename(temp_path, self._path)
        except Exception as e:
            logger.warning(
                'geocode_cache_save_failed',
                path=self._path,
                error=str(e),
                error_type=type(e).__name__,
            )
            # 저장 실패해도 계속 진행 (메모리 캐시는 유지됨)

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
        # 서킷 브레이커 추가 (안정성 개선)
        self.circuit_breaker = CircuitBreaker(
            failure_threshold=5,
            recovery_timeout=300,
        )

    async def geocode_batch(
        self,
        records: List[CertRecord],
        max_concurrency: int = 5,
    ) -> List[CertRecord]:
        """배치 지오코딩 (서킷 브레이커 적용)"""
        missing = [r for r in records if not r.has_coordinate()]

        if not missing:
            logger.info('geocode_skip_all_have_coords')
            return records

        logger.info(
            'geocode_start',
            missing=len(missing),
            total=len(records),
        )

        cid = self.settings.naver_client_id
        csec = self.settings.naver_client_secret

        if not cid or not csec:
            logger.warning('geocode_skip_no_api_key')
            return records

        api_calls = 0
        cache_hits = 0

        async with httpx.AsyncClient(timeout=15) as client:
            sem = asyncio.Semaphore(max_concurrency)

            async def geocode_one(record: CertRecord) -> None:
                nonlocal api_calls, cache_hits

                address = record.raw_address.strip()
                if not address:
                    return

                # 캐시 확인
                cached = self.cache.get(address)
                if cached is not None:
                    cache_hits += 1
                    self._apply_hit(record, cached)
                    return

                # 서킷 브레이커 확인 (안정성 개선)
                if self.circuit_breaker.is_open:
                    logger.warning(
                        'geocode_circuit_open_skipping',
                        address=address,
                    )
                    return

                # API 호출
                async with sem:
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
                            timeout=15,
                        )

                        if response.status_code == 200:
                            data = response.json()
                            hit = self._parse_naver_payload(data, address)
                            self.cache.set(address, hit)
                            self._apply_hit(record, hit)
                            # 성공 시 서킷 브레이커 리셋
                            self.circuit_breaker._on_success()
                        else:
                            self.cache.set(address, None)
                            # 실패 시 서킷 브레이커 카운트 증가
                            self.circuit_breaker._on_failure()

                        await asyncio.sleep(0.05)

                    except QuotaExceededError:
                        logger.error('geocode_quota_exceeded')
                        self.cache.save()
                        raise

                    except Exception as e:
                        logger.error(
                            'geocode_error',
                            address=address,
                            error=str(e),
                        )
                        self.cache.set(address, None)
                        # 예외 발생 시 서킷 브레이커 카운트 증가
                        self.circuit_breaker._on_failure()

            # 배치 처리
            chunk_size = 200
            for i in range(0, len(missing), chunk_size):
                chunk = missing[i:i + chunk_size]
                try:
                    await asyncio.gather(*[geocode_one(r) for r in chunk])
                except QuotaExceededError:
                    logger.error('geocode_batch_quota_exceeded')
                    break

                self.cache.save()
                logger.info(
                    'geocode_progress',
                    processed=min(i + chunk_size, len(missing)),
                    total=len(missing),
                    api_calls=api_calls,
                    cache_hits=cache_hits,
                )

        self.cache.save()
        logger.info(
            'geocode_complete',
            api_calls=api_calls,
            cache_hits=cache_hits,
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
        """지오코딩 결과 적용"""
        if not hit:
            return

        record.province = record.province or hit.get('province')
        record.municipality = record.municipality or hit.get('municipality')
        record.emd = record.emd or hit.get('emd')  # emd 보강
        record.road_name = record.road_name or hit.get('road_name')
        record.lot_num = record.lot_num or hit.get('lot_num')
        record.lat = hit.get('lat')
        record.lng = hit.get('lng')


# ═══════════════════════════════════════════════════════════
# 가게 이름 검증기 (네이버 + 카카오 동시)
# ═══════════════════════════════════════════════════════════

class StoreNameVerifier:
    """
    가게 이름 검증기 (네이버 + 카카오 맵 API 동시 사용)

    전략:
    1. 두 API를 병렬로 호출
    2. 먼저 성공한 결과 사용 (asyncio.wait + FIRST_COMPLETED)
    3. 둘 다 실패하면 원래 이름 유지
    4. 검증 결과는 store_name_quality 필드에 기록
    """

    NAVER_SEARCH_URL = 'https://openapi.naver.com/v1/search/local.json'
    KAKAO_SEARCH_URL = 'https://dapi.kakao.com/v2/local/search/keyword.json'

    def __init__(self):
        self.settings = get_settings()
        self.cache: Dict[str, Optional[str]] = {}
        self._cache_lock = threading.Lock()
        self._load_cache()

    def _load_cache(self) -> None:
        """검증 결과 캐시 로드"""
        cache_path = os.path.join(
            self.settings.cache_dir,
            'store_verify',
            'store_verify_cache.json',
        )
        if os.path.exists(cache_path):
            try:
                with open(cache_path, encoding='utf-8') as f:
                    self.cache = json.load(f)
                logger.info(
                    'store_verify_cache_loaded',
                    count=len(self.cache),
                )
            except Exception:
                self.cache = {}

    def _save_cache(self) -> None:
        """검증 결과 캐시 저장"""
        cache_path = os.path.join(
            self.settings.cache_dir,
            'store_verify',
            'store_verify_cache.json',
        )
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with self._cache_lock:
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(self.cache, f, ensure_ascii=False)

    def _get_cache(self, query: str) -> Optional[str]:
        """캐시에서 조회"""
        with self._cache_lock:
            return self.cache.get(query)

    def _set_cache(self, query: str, result: Optional[str]) -> None:
        """캐시에 저장"""
        with self._cache_lock:
            self.cache[query] = result

    async def verify_with_naver(
        self,
        client: httpx.AsyncClient,
        query: str,
    ) -> Optional[str]:
        """네이버 지도 API로 검증"""
        naver_client_id = self.settings.naver_client_id
        naver_client_secret = self.settings.naver_client_secret

        if not naver_client_id or not naver_client_secret:
            return None

        try:
            response = await client.get(
                self.NAVER_SEARCH_URL,
                headers={
                    'X-Naver-Client-Id': naver_client_id,
                    'X-Naver-Client-Secret': naver_client_secret,
                },
                params={
                    'query': query,
                    'display': 3,
                    'start': 1,
                },
                timeout=10.0,
            )

            if response.status_code == 200:
                data = response.json()
                items = data.get('items', [])

                for item in items:
                    title = item.get('title', '')
                    title = re.sub(r'<[^>]+>', '', title).strip()
                    if title and len(title) > 2:
                        return title

        except Exception as e:
            logger.error(
                'verify_naver_error',
                query=query,
                error=str(e),
            )

        return None

    async def verify_with_kakao(
        self,
        client: httpx.AsyncClient,
        query: str,
    ) -> Optional[str]:
        """카카오 지도 API로 검증"""
        kakao_api_key = self.settings.kakao_rest_api_key

        if not kakao_api_key:
            return None

        try:
            response = await client.get(
                self.KAKAO_SEARCH_URL,
                headers={
                    'Authorization': f'KakaoAK {kakao_api_key}',
                },
                params={
                    'query': query,
                    'size': 3,
                    'page': 1,
                },
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
            logger.error(
                'verify_kakao_error',
                query=query,
                error=str(e),
            )

        return None

    async def verify_store(
        self,
        client: httpx.AsyncClient,
        query: str,
    ) -> Optional[str]:
        """
        네이버 + 카카오 동시 검증 (첫 성공 결과 반환)

        전략:
        1. 캐시 확인
        2. 두 API를 병렬로 호출
        3. 먼저 성공한 결과 사용
        4. 둘 다 실패하면 None 반환
        """
        # 1. 캐시 확인
        cached = self._get_cache(query)
        if cached is not None:
            return cached if cached else None

        # 2. 병렬 호출
        naver_task = asyncio.create_task(self.verify_with_naver(client, query))
        kakao_task = asyncio.create_task(self.verify_with_kakao(client, query))

        # 3. 먼저 완료된 것 확인
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

        # 먼저 성공한 결과가 있으면 나머지 취소
        if result:
            for task in pending:
                task.cancel()
        else:
            # 둘 다 실패한 경우, 남은 결과 확인
            for task in pending:
                try:
                    task_result = await task
                    if task_result:
                        result = task_result
                        break
                except (asyncio.CancelledError, Exception):
                    pass

        # 4. 캐시에 저장
        self._set_cache(query, result)

        return result

    async def verify_batch(
        self,
        records: List[CertRecord],
        max_concurrency: int = 3,
    ) -> List[CertRecord]:
        """
        배치 검증

        Args:
            records: 검증할 레코드 목록
            max_concurrency: 최대 동시 요청 수

        Returns:
            검증된 레코드 목록
        """
        # 검증이 필요한 레코드 필터링
        needs_verification = [
            r for r in records
            if needs_store_verification(r.store_name_quality or 'valid')
        ]

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

                        logger.info(
                            'verify_store_name_updated',
                            old_name=old_name,
                            new_name=verified_name,
                            query=query[:50],
                        )
                    else:
                        failed_count += 1
                        logger.warning(
                            'verify_store_name_not_found',
                            store_name=record.raw_name,
                            query=query[:50],
                        )

                    # Rate limiting (API 부하 방지)
                    await asyncio.sleep(0.3)

            # 배치 처리
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

        # 캐시 저장
        self._save_cache()

        logger.info(
            'verify_store_names_complete',
            verified=verified_count,
            failed=failed_count,
            total=len(needs_verification),
        )

        return records


# ═══════════════════════════════════════════════════════════
# Silver Enricher (통합)
# ═══════════════════════════════════════════════════════════

class SilverEnricher:
    """
    Silver Layer 통합 보강기

    사용법:
        enricher = SilverEnricher()
        records = await enricher.enrich(records)

    처리 순서:
        1. 지오코딩 (좌표 없는 레코드)
        2. 좌표 이상치 탐지
        3. avoid_tags 자동 생성 (알레르기 기반)
        4. 가게 이름 품질 평가
        5. 가게 이름 검증 (네이버 + 카카오 동시)
    """

    def __init__(self):
        self.geocoder = Geocoder()
        self.store_verifier = StoreNameVerifier()

    async def enrich(
        self,
        records: List[CertRecord],
        verify_store_names: bool = True,
    ) -> List[CertRecord]:
        """
        데이터 보강

        1. 지오코딩 (좌표 없는 레코드)
        2. 좌표 이상치 탐지
        3. avoid_tags 자동 생성 (알레르기 기반)
        4. 가게 이름 품질 평가
        5. 가게 이름 검증 (네이버 + 카카오 동시)

        Args:
            records: 보강할 레코드 목록
            verify_store_names: 가게 이름 검증 활성화 여부

        Returns:
            보강된 레코드 목록
        """
        logger.info('silver_enrich_start', count=len(records))

        # 1. 지오코딩
        records = await self.geocoder.geocode_batch(records)

        # 2. 좌표 이상치 탐지
        records = detect_coordinate_anomalies(records)

        # 3. avoid_tags 자동 생성 (알레르기 기반)
        for record in records:
            if record.allergen_tags:
                record.avoid_tags = generate_avoid_tags(
                    allergen_tags=record.allergen_tags,
                    biz_type=record.biz_type,
                    main_menu=record.main_menu,
                    menus=record.menus,
                )

        # 4. 가게 이름 품질 평가
        for record in records:
            record.store_name_quality = assess_store_name_quality(
                record.raw_name,
                record.province,
                record.municipality,
            )

        # 5. 가게 이름 검증 (네이버 + 카카오 동시)
        if verify_store_names:
            needs_verify_count = sum(
                1 for r in records
                if needs_store_verification(r.store_name_quality or 'valid')
            )
            if needs_verify_count > 0:
                records = await self.store_verifier.verify_batch(records)
            else:
                logger.info('verify_store_names_skip_all_valid')

        logger.info('silver_enrich_complete', count=len(records))

        return records


async def enrich_coordinates(records: List[CertRecord]) -> List[CertRecord]:
    """
    좌표 보강 편의 함수 (기존 인터페이스 유지)

    Args:
        records: 좌표가 없는 레코드 목록

    Returns:
        좌표 보강된 레코드 목록
    """
    enricher = SilverEnricher()
    return await enricher.enrich(records, verify_store_names=False)


async def verify_store_names(records: List[CertRecord]) -> List[CertRecord]:
    """
    가게 이름 검증 편의 함수

    Args:
        records: 검증할 레코드 목록

    Returns:
        검증된 레코드 목록
    """
    enricher = SilverEnricher()
    return await enricher.enrich(records, verify_store_names=True)