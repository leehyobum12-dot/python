"""
Bareun Pipeline: Silver Enrichment - Geocoding & Coordinate Anomaly
====================================================================
- 지오코딩 캐시 관리 및 어댑터 호출 오케스트레이션
- 좌표 이상치 탐지 (도심/외곽 반경 기반)
- I/O는 adapters/geocode_client.py에 위임, 이 모듈은 오케스트레이션만 담당

수정 사항 (Autofix):
  - enrich_coordinates 진입점 함수에 `force` 매개변수 추가 및 하위 서비스로 위임
  - Pyright/Pylance "No parameter named 'force'" 에러 해결
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
from math import asin, cos, radians, sin, sqrt
from typing import Optional

import httpx

from ...domain.record import CertRecord
from ...adapters.geocode_client import GeocodeAdapter, GeocodeResult
from ...observability.logging import get_logger
from ...settings import get_settings

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════
# 좌표 이상치 탐지 상수
# ═══════════════════════════════════════════════════════════

PROVINCE_CENTERS: dict[str, tuple[float, float]] = {
    '서울특별시': (37.5665, 126.9780), '부산광역시': (35.1796, 129.0756),
    '대구광역시': (35.8714, 128.6014), '인천광역시': (37.4563, 126.7052),
    '광주광역시': (35.1595, 126.8526), '대전광역시': (36.3504, 127.3845),
    '울산광역시': (35.5384, 129.3114), '세종특별자치시': (36.4800, 127.2890),
    '경기도': (37.4138, 127.5171), '강원특별자치도': (37.8228, 128.1555),
    '충청북도': (36.8001, 127.7200), '충청남도': (36.5184, 126.8000),
    '전북특별자치도': (35.7175, 127.1530), '전라남도': (35.1595, 126.8526),
    '경상북도': (36.4919, 128.8889), '경상남도': (35.4606, 128.2132),
    '제주특별자치도': (33.4996, 126.5312),
}

PROVINCE_RADIUS_M: dict[str, int] = {
    '서울특별시': 20_000, '세종특별자치시': 20_000,
    '부산광역시': 40_000, '대구광역시': 40_000,
    '인천광역시': 50_000, '광주광역시': 30_000,
    '대전광역시': 30_000, '울산광역시': 40_000,
    '경기도': 80_000, '강원특별자치도': 150_000,
    '충청북도': 80_000, '충청남도': 80_000,
    '전북특별자치도': 80_000, '전라남도': 120_000,
    '경상북도': 150_000, '경상남도': 120_000,
    '제주특별자치도': 80_000,
}

MUNICIPALITY_EXCEPTIONS: dict[tuple[str, str], int] = {
    ('대구광역시', '군위군'): 60_000, ('경기도', '파주시'): 100_000,
    ('경기도', '연천군'): 120_000, ('인천광역시', '강화군'): 80_000,
    ('인천광역시', '옹진군'): 200_000, ('경상북도', '울릉군'): 250_000,
    ('경상북도', '울진군'): 180_000, ('경상남도', '거제시'): 140_000,
    ('경상남도', '남해군'): 140_000, ('경상남도', '통영시'): 140_000,
    ('전라남도', '신안군'): 160_000, ('전라남도', '진도군'): 160_000,
    ('전라남도', '완도군'): 160_000, ('강원특별자치도', '고성군'): 180_000,
    ('강원특별자치도', '인제군'): 160_000, ('경기도', '가평군'): 100_000,
    ('경기도', '양평군'): 90_000, ('경상북도', '봉화군'): 180_000,
    ('경상북도', '영덕군'): 170_000, ('전라남도', '여수시'): 150_000,
    ('전라남도', '고흥군'): 150_000,
}


# ═══════════════════════════════════════════════════════════
# 좌표 유틸리티
# ═══════════════════════════════════════════════════════════

def haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """두 좌표 사이 거리 (미터)"""
    R = 6371000
    lat1, lng1, lat2, lng2 = map(radians, [lat1, lng1, lat2, lng2])
    dlat, dlng = lat2 - lat1, lng2 - lng1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlng / 2) ** 2
    return R * 2 * asin(sqrt(a))


def detect_coordinate_anomalies(
    records: list[CertRecord],
    cross_validate: bool = True,
) -> list[CertRecord]:
    """좌표 이상치 탐지 + 교차 검증"""
    anomalies = 0
    cross_validation_warnings = 0

    for record in records:
        if not record.lat or not record.lng or not record.province:
            continue

        center = PROVINCE_CENTERS.get(record.province)
        if center:
            key = (record.province, record.municipality or '')
            max_radius = MUNICIPALITY_EXCEPTIONS.get(
                key, PROVINCE_RADIUS_M.get(record.province, 100_000)
            )
            dist = haversine(record.lat, record.lng, center[0], center[1])

            if dist > max_radius:
                anomalies += 1
                if anomalies <= 10:
                    logger.warning(
                        'coordinate_anomaly_province_center',
                        name=record.raw_name, province=record.province,
                        distance_km=round(dist / 1000, 1),
                        max_km=max_radius // 1000,
                    )
                record.lat = None
                record.lng = None
                continue

        if cross_validate and record.raw and isinstance(record.raw, dict):
            for src_key, threshold in [
                (('general_lat', 'general_lng'), 1000),
                (('tourist_lat', 'tourist_lng'), 500),
            ]:
                src_lat = record.raw.get(src_key[0])
                src_lng = record.raw.get(src_key[1])
                if src_lat and src_lng:
                    dist = haversine(record.lat, record.lng, src_lat, src_lng)
                    if dist > threshold:
                        cross_validation_warnings += 1
                        if cross_validation_warnings <= 10:
                            logger.warning(
                                'coordinate_cross_validation_warning',
                                name=record.raw_name,
                                source=src_key[0], distance_m=round(dist, 1),
                            )

    if anomalies > 0:
        logger.info('coordinate_anomalies_detected', count=anomalies)
    if cross_validation_warnings > 0:
        logger.info('coordinate_cross_validation_warnings', count=cross_validation_warnings)

    return records


# ═══════════════════════════════════════════════════════════
# 지오코딩 캐시
# ═══════════════════════════════════════════════════════════

class GeocodeCache:
    """지오코딩 캐시 (파일 기반)"""

    def __init__(self, cache_path: str):
        self._path = cache_path
        self._cache: dict[str, Optional[dict]] = {}
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
                os.replace(temp_path, self._path)
        except Exception as e:
            logger.warning('geocode_cache_save_failed', path=self._path, error=str(e))

    def get(self, address: str) -> Optional[dict]:
        with self._lock:
            return self._cache.get(address)

    def set(self, address: str, result: Optional[dict]) -> None:
        with self._lock:
            self._cache[address] = result


# ═══════════════════════════════════════════════════════════
# 지오코딩 서비스 (어댑터 오케스트레이션)
# ═══════════════════════════════════════════════════════════

class GeocodeService:
    """네이버 지오코딩 어댑터 기반 좌표 보강 서비스"""

    def __init__(self):
        self.settings = get_settings()
        self.adapter = GeocodeAdapter(settings=self.settings)
        self.cache = GeocodeCache(
            os.path.join(self.settings.cache_dir, 'geocode', 'geocode_cache.json')
        )

    async def geocode_batch(
        self,
        records: list[CertRecord],
        max_concurrency: int = 3,
        force: bool = False,
    ) -> list[CertRecord]:
        """배치 지오코딩 (캐시 우선 + 어댑터 위임)"""
        targets = [
            r for r in records
            if force or not r.has_coordinate()
        ]
        targets = [r for r in targets if r.raw_address and r.raw_address.strip()]

        if not targets:
            logger.info('geocode_skip_no_targets')
            return records

        logger.info('geocode_start', targets=len(targets), total=len(records), force=force)

        if not self.settings.naver_client_id or not self.settings.naver_client_secret:
            logger.warning('geocode_skip_no_api_key')
            return records

        api_calls = 0
        cache_hits = 0

        async with httpx.AsyncClient(timeout=30) as client:
            sem = asyncio.Semaphore(max_concurrency)

            async def geocode_one(record: CertRecord) -> None:
                nonlocal api_calls, cache_hits
                address = record.raw_address.strip()

                cached_dict = self.cache.get(address)
                if cached_dict is not None and not force:
                    cache_hits += 1
                    self._apply_cached(record, cached_dict)
                    return

                async with sem:
                    result: Optional[GeocodeResult] = await self.adapter.geocode(address, client)
                    
                    if result:
                        api_calls += 1
                        hit_dict = {
                            'province': result.province,
                            'municipality': result.municipality,
                            'emd': result.emd,
                            'road_name': result.road_name,
                            'lot_num': result.lot_num,
                            'lat': result.lat,
                            'lng': result.lng,
                        }
                        self.cache.set(address, hit_dict)
                        self._apply_hit(record, result)
                    else:
                        self.cache.set(address, None)

            chunk_size = 200
            for i in range(0, len(targets), chunk_size):
                chunk = targets[i:i + chunk_size]
                await asyncio.gather(*[geocode_one(r) for r in chunk])
                self.cache.save()
                logger.info(
                    'geocode_progress',
                    processed=min(i + chunk_size, len(targets)),
                    total=len(targets), api_calls=api_calls, cache_hits=cache_hits,
                )

        self.cache.save()
        logger.info('geocode_complete', api_calls=api_calls, cache_hits=cache_hits)
        return records

    def _apply_hit(self, record: CertRecord, result: GeocodeResult) -> None:
        """지오코딩 결과 적용 (GeocodeResult 불변 객체 직접 사용)"""
        record.province = result.province or record.province
        record.municipality = result.municipality or record.municipality
        record.emd = result.emd or record.emd
        record.road_name = result.road_name or record.road_name
        record.lot_num = result.lot_num or record.lot_num
        record.lat = result.lat
        record.lng = result.lng

    def _apply_cached(self, record: CertRecord, cached: dict) -> None:
        """캐시된 dict 결과 적용"""
        record.province = cached.get('province') or record.province
        record.municipality = cached.get('municipality') or record.municipality
        record.emd = cached.get('emd') or record.emd
        record.road_name = cached.get('road_name') or record.road_name
        record.lot_num = cached.get('lot_num') or record.lot_num
        record.lat = cached.get('lat')
        record.lng = cached.get('lng')


# ═══════════════════════════════════════════════════════════
# 진입점 (Autofix 적용)
# ═══════════════════════════════════════════════════════════

async def enrich_coordinates(
    records: list[CertRecord], 
    force: bool = False,
) -> list[CertRecord]:
    """
    지오코딩 + 좌표 이상치 탐지 진입점
    
    Args:
        records: 대상 레코드 목록
        force: True면 기존 좌표/캐시를 무시하고 강제 지오코딩 수행
    """
    service = GeocodeService()
    records = await service.geocode_batch(records, force=force)
    return detect_coordinate_anomalies(records)