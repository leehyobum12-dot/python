# data_pipeline/enrich/geocoder.py
import requests, os, json, time
from typing import List
from adapters.base import CertRecord

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(BASE_DIR, '..', 'out', 'geocode_cache.json')
_cache = {}


class QuotaExceededError(Exception):
    """네이버 API 일일 쿼터 한도 초과 시 발생하는 커스텀 예외"""
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
    with open(CACHE_PATH, 'w', encoding='utf-8') as f:
        json.dump(_cache, f, ensure_ascii=False)


def is_valid_coord(lat, lng) -> bool:
    try:
        lat, lng = float(lat), float(lng)
        return 33.0 <= lat <= 38.6 and 124.5 <= lng <= 132.0
    except Exception:
        return False


def geocode_naver(address, client_id, client_secret, retries=2):
    if not address or not address.strip():
        return None
        
    url = f"https://maps.apigw.ntruss.com/map-geocode/v2/geocode?query={address}"
    headers = {
        "X-NCP-APIGW-API-KEY-ID": client_id,
        "X-NCP-APIGW-API-KEY": client_secret,
        "Referer": "http://localhost"
    }
    
    for attempt in range(retries + 1):
        try:
            res = requests.get(url, headers=headers, timeout=10)
            
            # ✅ [방어 로직 1] HTTP 429 (Too Many Requests) 체크
            if res.status_code == 429:
                raise QuotaExceededError()
                
            if res.status_code != 200:
                if attempt < retries:
                    time.sleep(1.0 * (attempt + 1))
                    continue
                return None
                
            data = res.json()
            
            # ✅ [방어 로직 2] JSON 응답 내 에러 코드 체크 (네이버 쿼터 초과 등)
            error_info = data.get('error', {})
            error_code = str(error_info.get('errorCode', '')).upper()
            if 'QUOTA' in error_code or 'LIMIT' in error_code:
                raise QuotaExceededError()
                
            if not data.get('addresses'):
                return None
                
            addr = data['addresses'][0]
            province = municipality = emd = ""
            for elem in addr.get('addressElements', []):
                types = elem.get('types', [])
                if "SIDO" in types: 
                    province = elem.get('longName', '')
                elif "SIGUGUN" in types: 
                    municipality = elem.get('longName', '')
                elif "DONGMYUN" in types: 
                    emd = elem.get('longName', '')
                    
            return {
                'province': province, 
                'municipality': municipality, 
                'emd': emd,
                'road_name': addr.get('roadAddress', address),
                'lot_num': addr.get('jibunAddress', ''),
                'lat': float(addr.get('y', 0)), 
                'lng': float(addr.get('x', 0))
            }
            
        except QuotaExceededError:
            # 쿼터 에러는 재시도하지 않고 즉시 상위 루프로 전파(Propagate)
            raise
        except Exception as e:
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            return None
    return None


def geocode_batch(records: List[CertRecord]) -> List[CertRecord]:
    client_id = os.getenv('NAVER_CLIENT_ID', '')
    client_secret = os.getenv('NAVER_CLIENT_SECRET', '')
    if not client_id or not client_secret:
        print("⚠️ NAVER 키 미설정 — 좌표 보강 스킵")
        return records

    _load_cache()
    missing = [r for r in records if not r.lat or not r.lng or not is_valid_coord(r.lat, r.lng)]
    print(f"📍 좌표 보강 대상: {len(missing)}/{len(records)}건")

    api_calls = cache_hits = 0
    for i, record in enumerate(missing):
        key = record.raw_address.strip()
        
        if key in _cache:
            hit = _cache[key]
            cache_hits += 1
        else:
            try:
                hit = geocode_naver(key, client_id, client_secret)
                _cache[key] = hit
                api_calls += 1
                time.sleep(0.15)
            except QuotaExceededError:
                # ✅ [방어 로직 3] 쿼터 소진 시 우아한 중단(Graceful Degradation)
                print("\n🛑 네이버 일일 쿼터 소진 — 현재까지의 캐시를 저장하고 안전하게 중단합니다.")
                print("   💡 내일 자정(KST) 이후 쿼터가 갱신되면, 동일한 명령어로 재실행하십시오.")
                _save_cache()
                break  # 루프를 깨고 정상 종료 (프로세스 크래시 방지)
                
        if hit:
            record.province = record.province or hit.get('province')
            record.municipality = record.municipality or hit.get('municipality')
            record.emd = record.emd or hit.get('emd')
            record.road_name = record.road_name or hit.get('road_name')
            record.lot_num = record.lot_num or hit.get('lot_num')
            record.lat = hit.get('lat')
            record.lng = hit.get('lng')
            
        if (i + 1) % 100 == 0:
            print(f"   진행: {i+1}/{len(missing)} (API {api_calls} / 캐시 {cache_hits})")
            _save_cache()

    _save_cache()
    print(f"✅ 좌표 보강 완료: API {api_calls}회 / 캐시 {cache_hits}회")
    return records