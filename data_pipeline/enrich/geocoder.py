# data_pipeline/enrich/geocoder.py
"""네이버 지오코딩 API 기반 좌표 보강 모듈 (동기/비동기)"""

import os
import json
import time
import threading
import asyncio
import socket
import requests
import httpx
from math import radians, cos, sin, asin, sqrt
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter

from adapters.base import CertRecord, extract_emd_from_addr, is_road_name, is_emd_token


# ─────────────── 전역 설정 ───────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(BASE_DIR, '..', 'out', 'geocode_cache.json')

_cache: Dict[str, Any] = {}
_cache_lock = threading.Lock()

_session = requests.Session()
_adapter = HTTPAdapter(
    pool_connections=20,
    pool_maxsize=50,
    max_retries=3,
    pool_block=False,
)
_session.mount('http://', _adapter)
_session.mount('https://', _adapter)

NAVER_URL = "https://maps.apigw.ntruss.com/map-geocode/v2/geocode"


# ─────────────── 좌표 이상치 탐지용 상수 ───────────────
PROVINCE_CENTERS = {
    '서울특별시': (37.5665, 126.9780), '부산광역시': (35.1796, 129.0756),
    '대구광역시': (35.8714, 128.6014), '인천광역시': (37.4563, 126.7052),
    '광주광역시': (35.1595, 126.8526), '대전광역시': (36.3504, 127.3845),
    '울산광역시': (35.5384, 129.3114), '세종특별자치시': (36.4800, 127.2890),
    '경기도': (37.4138, 127.5171), '강원특별자치도': (37.8228, 128.1555),
    '충청북도': (36.8001, 127.7200), '충청남도': (36.5184, 126.8000),
    '전북특별자치도': (35.7175, 127.1530), '전남광주통합특별시': (35.1595, 126.8526),
    '경상북도': (36.4919, 128.8889), '경상남도': (35.4606, 128.2132),
    '제주특별자치도': (33.4996, 126.5312),
}

PROVINCE_RADIUS_M = {
    '서울특별시': 20_000, '세종특별자치시': 20_000,
    '부산광역시': 40_000, '대구광역시': 40_000,
    '인천광역시': 50_000, '광주광역시': 30_000,
    '대전광역시': 30_000, '울산광역시': 40_000,
    '경기도': 80_000, '강원특별자치도': 150_000,
    '충청북도': 80_000, '충청남도': 80_000,
    '전북특별자치도': 80_000, '전남광주통합특별시': 120_000,
    '경상북도': 150_000, '경상남도': 120_000,
    '제주특별자치도': 80_000,
}

# ✅ [신규] 시군구별 예외 (실제 지역이지만 중심에서 먼 곳)
MUNICIPALITY_EXCEPTIONS = {
    ('대구광역시', '군위군'): 60_000,      # 2023년 편입
    ('경기도', '파주시'): 100_000,         # 남북 접경
    ('경기도', '연천군'): 120_000,         # 남북 접경
    ('인천광역시', '강화군'): 80_000,      # 도서 지역
    ('인천광역시', '옹진군'): 200_000,     # 도서 지역 (백령도 등)
    ('경상북도', '울릉군'): 250_000,       # 도서 지역 (울릉도)
    ('경상북도', '울진군'): 180_000,       # 동해안 멀리
    ('경상남도', '거제시'): 140_000,       # 도서 지역
    ('경상남도', '남해군'): 140_000,       # 도서 지역
    ('경상남도', '통영시'): 140_000,       # 도서 지역
    ('전남광주통합특별시', '신안군'): 160_000,  # 도서 지역
    ('전남광주통합특별시', '진도군'): 160_000,
    ('전남광주통합특별시', '완도군'): 160_000,
    ('강원특별자치도', '고성군'): 180_000, # 남북 접경
    ('강원특별자치도', '인제군'): 160_000, # 산간 지역
    
     # ✅ 신규 추가
    ('충청남도', '금산군'): 100_000,      # 충남 동쪽 끝 (전북 경계)
    ('충청남도', '서천군'): 100_000,      # 충남 서남쪽 끝
    ('경기도', '가평군'): 100_000,        # 경기 동북쪽 끝
    ('경기도', '양평군'): 90_000,         # 경기 동쪽
    ('경상북도', '봉화군'): 180_000,      # 경북 북쪽 끝
    ('경상북도', '영덕군'): 170_000,      # 경북 동해안
    ('전남광주통합특별시', '여수시'): 150_000,  # 여수 반도
    ('전남광주통합특별시', '고흥군'): 150_000,  # 고흥 반도
}


def _haversine(lat1, lng1, lat2, lng2):
    """두 좌표 사이 거리 (미터)"""
    R = 6371000
    lat1, lng1, lat2, lng2 = map(radians, [lat1, lng1, lat2, lng2])
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    a = sin(dlat/2)**2 + cos(lat1)*cos(lat2)*sin(dlng/2)**2
    return R * 2 * asin(sqrt(a))


def detect_coordinate_anomalies(records: List[CertRecord]) -> List[CertRecord]:
    """주소-좌표 불일치 탐지 (시군구 예외 반영)"""
    anomalies = 0
    for r in records:
        if not r.lat or not r.lng or not r.province:
            continue
        center = PROVINCE_CENTERS.get(r.province)
        if not center:
            continue
        
        # ✅ 시군구 예외 체크
        key = (r.province, r.municipality)
        if key in MUNICIPALITY_EXCEPTIONS:
            max_radius = MUNICIPALITY_EXCEPTIONS[key]
        else:
            max_radius = PROVINCE_RADIUS_M.get(r.province, 100_000)
        
        dist = _haversine(r.lat, r.lng, center[0], center[1])
        if dist > max_radius:
            anomalies += 1
            if anomalies <= 10:
                print(f"   ⚠️ 좌표 이상치: {r.raw_name} ({r.province} {r.municipality}) "
                      f"- 중심에서 {dist/1000:.1f}km (허용: {max_radius/1000:.0f}km)")
            r.lat, r.lng = None, None

    if anomalies > 0:
        print(f"🔍 좌표 이상치 탐지: {anomalies}건 제거")
    return records


# ─────────────── 예외 클래스 ───────────────
class QuotaExceededError(Exception):
    """네이버 일일 쿼터 소진"""
    pass


# ─────────────── DNS Fallback 유틸리티 ───────────────
def _flush_dns_cache():
    """DNS 캐시 플러시 (Windows/Linux/macOS)"""
    import platform
    import subprocess
    system = platform.system()
    try:
        if system == 'Windows':
            subprocess.run(['ipconfig', '/flushdns'], 
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        elif system == 'Linux':
            subprocess.run(['systemd-resolve', '--flush-caches'], 
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        elif system == 'Darwin':  # macOS
            subprocess.run(['dscacheutil', '-flushcache'], 
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
    except Exception:
        pass  # 실패해도 무시


def _retry_with_dns_fallback(func, *args, retries=3, **kwargs):
    """DNS 실패 시 재시도 + DNS 캐시 플러시"""
    for attempt in range(retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            err_str = str(e).lower()
            if 'getaddrinfo failed' in err_str or 'nameresolutionerror' in err_str:
                if attempt < retries - 1:
                    print(f"   🔄 DNS 실패 → 캐시 플러시 후 재시도 ({attempt+1}/{retries})")
                    _flush_dns_cache()
                    time.sleep(2 ** attempt)
                    continue
            raise


# ─────────────── 캐시 관리 ───────────────
def _load_cache():
    global _cache
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, 'r', encoding='utf-8') as f:
                _cache = json.load(f)
            print(f"💾 지오코딩 캐시 로드: {len(_cache)}건")
        except Exception:
            _cache = {}


def _save_cache():
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with _cache_lock:
        with open(CACHE_PATH, 'w', encoding='utf-8') as f:
            json.dump(_cache, f, ensure_ascii=False)


def is_valid_coord(lat, lng) -> bool:
    try:
        lat, lng = float(lat), float(lng)
        return 33.0 <= lat <= 38.6 and 124.5 <= lng <= 132.0
    except Exception:
        return False


def _parse_naver_payload(data: dict, address: str) -> Optional[dict]:
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
    return {
        'province': province, 'municipality': municipality, 'emd': emd,
        'road_name': a.get('roadAddress', address),
        'lot_num': a.get('jibunAddress', ''),
        'lat': float(a.get('y', 0)), 'lng': float(a.get('x', 0)),
    }


def _check_quota(status_code: int, data: dict):
    if status_code == 429:
        raise QuotaExceededError()
    err = str((data.get('error') or {}).get('errorCode', '')).upper()
    if 'QUOTA' in err or 'LIMIT' in err:
        raise QuotaExceededError()


def geocode_naver(address, client_id, client_secret, retries=2):
    if not address or not address.strip():
        return None
    headers = {
        "X-NCP-APIGW-API-KEY-ID": client_id,
        "X-NCP-APIGW-API-KEY": client_secret,
        "Referer": "http://localhost",
    }
    for attempt in range(retries + 1):
        try:
            # ✅ DNS fallback 래핑
            def _do_request():
                return _session.get(NAVER_URL, params={'query': address},
                                   headers=headers, timeout=15)
            res = _retry_with_dns_fallback(_do_request)
            
            if res.status_code != 200:
                _check_quota(res.status_code, {})
                if attempt < retries:
                    time.sleep(1.0 * (attempt + 1)); continue
                return None
            data = res.json()
            _check_quota(res.status_code, data)
            return _parse_naver_payload(data, address)
        except QuotaExceededError:
            raise
        except Exception:
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1)); continue
            return None
    return None


async def _geocode_naver_async(client, address, cid, csec):
    headers = {
        "X-NCP-APIGW-API-KEY-ID": cid,
        "X-NCP-APIGW-API-KEY": csec,
        "Referer": "http://localhost",
    }
    res = await client.get(NAVER_URL, params={'query': address}, headers=headers, timeout=15)
    if res.status_code != 200:
        _check_quota(res.status_code, {})
        return None
    data = res.json()
    _check_quota(res.status_code, data)
    return _parse_naver_payload(data, address)


def _apply_hit(record: CertRecord, hit: Optional[dict]):
    if not hit:
        return
    record.province = record.province or hit.get('province')
    record.municipality = record.municipality or hit.get('municipality')
    record.road_name = record.road_name or hit.get('road_name')
    record.lot_num = record.lot_num or hit.get('lot_num')
    record.lat = hit.get('lat')
    record.lng = hit.get('lng')

    emd_from_lot = extract_emd_from_addr(record.lot_num) if record.lot_num else ''
    emd_from_api = hit.get('emd') or ''
    emd_from_orig = extract_emd_from_addr(record.raw_address) if record.raw_address else ''

    api_is_valid = (bool(emd_from_api) and is_emd_token(emd_from_api)
                    and not is_road_name(emd_from_api))

    if not record.emd or is_road_name(record.emd):
        record.emd = (emd_from_lot
                      or (emd_from_api if api_is_valid else '')
                      or emd_from_orig)


def _geocode_batch_sync(missing, cid, csec):
    api_calls = cache_hits = 0
    def worker(rec):
        key = rec.raw_address.strip()
        with _cache_lock:
            if key in _cache:
                return rec, _cache[key], True
        hit = geocode_naver(key, cid, csec)
        with _cache_lock:
            _cache[key] = hit
        return rec, hit, False

    with ThreadPoolExecutor(max_workers=5) as ex:
        futs = {ex.submit(worker, r): r for r in missing}
        done = 0
        for fut in as_completed(futs):
            try:
                rec, hit, cached = fut.result()
            except QuotaExceededError:
                print("\n🛑 쿼터 소진 — 캐시 저장 후 중단")
                _save_cache()
                break
            if cached: cache_hits += 1
            else: api_calls += 1
            _apply_hit(rec, hit)
            done += 1
            if done % 500 == 0:
                print(f"   진행: {done}/{len(missing)} (API {api_calls} / 캐시 {cache_hits})")
                _save_cache()
    print(f"✅ 좌표 보강 완료: API {api_calls}회 / 캐시 {cache_hits}회")


def _geocode_batch_async(missing, cid, csec, max_concurrency=3):
    api_calls = cache_hits = 0

    async def run():
        nonlocal api_calls, cache_hits
        async with httpx.AsyncClient(timeout=15) as client:
            sem = asyncio.Semaphore(max_concurrency)

            async def one(rec):
                key = rec.raw_address.strip()
                with _cache_lock:
                    if key in _cache:
                        return rec, _cache[key], True
                async with sem:
                    hit = await _geocode_naver_async(client, key, cid, csec)
                    await asyncio.sleep(0.05)
                with _cache_lock:
                    _cache[key] = hit
                return rec, hit, False

            i = 0
            while i < len(missing):
                chunk = missing[i:i + 200]
                try:
                    results = await asyncio.gather(*[one(r) for r in chunk])
                except QuotaExceededError:
                    print("\n🛑 쿼터 소진 — 캐시 저장 후 중단")
                    _save_cache()
                    return
                for rec, hit, cached in results:
                    if cached: cache_hits += 1
                    else: api_calls += 1
                    _apply_hit(rec, hit)
                i += 200
                print(f"   진행: {min(i, len(missing))}/{len(missing)} "
                      f"(API {api_calls} / 캐시 {cache_hits})")
                _save_cache()

    asyncio.run(run())
    print(f"✅ 좌표 보강 완료(비동기): API {api_calls}회 / 캐시 {cache_hits}회")


def geocode_batch(records: List[CertRecord], async_mode: bool = True,
                  max_concurrency: int = 5) -> List[CertRecord]:
    cid = os.getenv('NAVER_CLIENT_ID', '')
    csec = os.getenv('NAVER_CLIENT_SECRET', '')
    if not cid or not csec:
        print("⚠️ NAVER 키 미설정 — 좌표 보강 스킵")
        return records

    _load_cache()
    missing = [r for r in records
               if not r.lat or not r.lng or not is_valid_coord(r.lat, r.lng)]
    print(f"📍 좌표 보강 대상: {len(missing)}/{len(records)}건")
    if not missing:
        return records

    if async_mode:
        _geocode_batch_async(missing, cid, csec, max_concurrency)
    else:
        _geocode_batch_sync(missing, cid, csec)
    _save_cache()
    return records