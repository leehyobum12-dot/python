# enrich/geocoder.py — 병렬 지오코딩 추가
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# ✅ 스레드 안전 락 (캐시 동시 쓰기 방지)
_cache_lock = threading.Lock()


def _geocode_parallel(records: List[CertRecord], client_id: str, client_secret: str, max_workers: int = 5):
    """병렬 지오코딩 (네이버 API Rate Limit 고려 5 워커)"""
    results = {}
    
    def worker(idx: int, record: CertRecord):
        key = record.raw_address.strip()
        if key in _cache:
            return idx, _cache[key], True
        hit = geocode_naver(key, client_id, client_secret)
        with _cache_lock:
            _cache[key] = hit
        return idx, hit, False
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(worker, i, r) for i, r in enumerate(records)]
        completed = 0
        for future in as_completed(futures):
            idx, hit, was_cached = future.result()
            results[idx] = (hit, was_cached)
            completed += 1
            if completed % 100 == 0:
                print(f"   진행: {completed}/{len(records)}")
                _save_cache()
    return results


def geocode_batch(records: List[CertRecord], parallel: bool = True) -> List[CertRecord]:
    client_id = os.getenv('NAVER_CLIENT_ID', '')
    client_secret = os.getenv('NAVER_CLIENT_SECRET', '')
    if not client_id or not client_secret:
        print("⚠️ NAVER 키 미설정 — 좌표 보강 스킵")
        return records

    _load_cache()
    missing = [r for r in records if not r.lat or not r.lng or not is_valid_coord(r.lat, r.lng)]
    print(f"📍 좌표 보강 대상: {len(missing)}/{len(records)}건")

    if parallel and len(missing) > 50:
        print(f"   🚀 병렬 지오코딩 시작 (5 워커)")
        results = _geocode_parallel(missing, client_id, client_secret, max_workers=5)
        
        for i, record in enumerate(missing):
            hit, _ = results.get(i, (None, False))
            if hit:
                record.province = record.province or hit.get('province')
                record.municipality = record.municipality or hit.get('municipality')
                record.emd = record.emd or hit.get('emd')
                record.road_name = record.road_name or hit.get('road_name')
                record.lot_num = record.lot_num or hit.get('lot_num')
                record.lat = hit.get('lat')
                record.lng = hit.get('lng')
    else:
        # 기존 순차 로직 (fallback)
        for record in missing:
            key = record.raw_address.strip()
            if key in _cache:
                hit = _cache[key]
            else:
                try:
                    hit = geocode_naver(key, client_id, client_secret)
                    _cache[key] = hit
                    time.sleep(0.15)
                except QuotaExceededError:
                    print("\n🛑 네이버 쿼터 소진 — 캐시 저장 후 중단")
                    _save_cache()
                    break
            if hit:
                record.province = record.province or hit.get('province')
                record.municipality = record.municipality or hit.get('municipality')
                record.emd = record.emd or hit.get('emd')
                record.road_name = record.road_name or hit.get('road_name')
                record.lot_num = record.lot_num or hit.get('lot_num')
                record.lat = hit.get('lat')
                record.lng = hit.get('lng')

    _save_cache()
    print(f"✅ 좌표 보강 완료")
    return records