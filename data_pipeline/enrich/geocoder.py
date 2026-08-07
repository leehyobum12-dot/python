# data_pipeline/enrich/geocoder.py
import os, json, time, threading, asyncio
import requests, httpx
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from adapters.base import CertRecord

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(BASE_DIR, '..', 'out', 'geocode_cache.json')

_cache: Dict[str, Any] = {}
_cache_lock = threading.Lock()

# Connection Pooling (동기용)
_session = requests.Session()
_adapter = requests.adapters.HTTPAdapter(
    pool_connections=20, pool_maxsize=50, max_retries=3, pool_block=False)
_session.mount('http://', _adapter)
_session.mount('https://', _adapter)

NAVER_URL = "https://maps.apigw.ntruss.com/map-geocode/v2/geocode"


class QuotaExceededError(Exception):
    """네이버 일일 쿼터 소진"""
    pass


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
        if 'SIDO' in t: province = el.get('longName', '')
        elif 'SIGUGUN' in t: municipality = el.get('longName', '')
        elif 'DONGMYUN' in t: emd = el.get('longName', '')
    return {'province': province, 'municipality': municipality, 'emd': emd,
            'road_name': a.get('roadAddress', address),
            'lot_num': a.get('jibunAddress', ''),
            'lat': float(a.get('y', 0)), 'lng': float(a.get('x', 0))}


def _check_quota(status_code: int, data: dict):
    if status_code == 429:
        raise QuotaExceededError()
    err = str((data.get('error') or {}).get('errorCode', '')).upper()
    if 'QUOTA' in err or 'LIMIT' in err:
        raise QuotaExceededError()


# ─────────────── 동기 호출 ───────────────
def geocode_naver(address, client_id, client_secret, retries=2):
    if not address or not address.strip():
        return None
    headers = {"X-NCP-APIGW-API-KEY-ID": client_id,
               "X-NCP-APIGW-API-KEY": client_secret,
               "Referer": "http://localhost"}
    for attempt in range(retries + 1):
        try:
            res = _session.get(NAVER_URL, params={'query': address},
                               headers=headers, timeout=10)
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


# ─────────────── 비동기 호출 (Phase 2) ───────────────
async def _geocode_naver_async(client, address, cid, csec):
    headers = {"X-NCP-APIGW-API-KEY-ID": cid,
               "X-NCP-APIGW-API-KEY": csec, "Referer": "http://localhost"}
    res = await client.get(NAVER_URL, params={'query': address}, headers=headers)
    if res.status_code != 200:
        _check_quota(res.status_code, {})
        return None
    data = res.json()
    _check_quota(res.status_code, data)
    return _parse_naver_payload(data, address)


def _apply_hit(record: CertRecord, hit: Optional[dict]):
    if hit:
        record.province = record.province or hit.get('province')
        record.municipality = record.municipality or hit.get('municipality')
        record.emd = record.emd or hit.get('emd')
        record.road_name = record.road_name or hit.get('road_name')
        record.lot_num = record.lot_num or hit.get('lot_num')
        record.lat = hit.get('lat')
        record.lng = hit.get('lng')


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


def _geocode_batch_async(missing, cid, csec, max_concurrency=10):
    api_calls = cache_hits = 0

    async def run():
        nonlocal api_calls, cache_hits
        async with httpx.AsyncClient(timeout=10) as client:
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
                  max_concurrency: int = 10) -> List[CertRecord]:
    cid = os.getenv('NAVER_CLIENT_ID', '')
    csec = os.getenv('NAVER_CLIENT_SECRET', '')
    if not cid or not csec:
        print("⚠️ NAVER 키 미설정 — 좌표 보강 스킵")
        return records

    _load_cache()
    missing = [r for r in records if not r.lat or not r.lng or not is_valid_coord(r.lat, r.lng)]
    print(f"📍 좌표 보강 대상: {len(missing)}/{len(records)}건")
    if not missing:
        return records

    if async_mode:
        _geocode_batch_async(missing, cid, csec, max_concurrency)
    else:
        _geocode_batch_sync(missing, cid, csec)
    _save_cache()
    return records