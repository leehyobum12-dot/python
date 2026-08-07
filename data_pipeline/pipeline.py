# data_pipeline/pipeline.py
import os, sys, re
from typing import List, Dict
from collections import Counter
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from adapters.base import CertRecord
from adapters.safe_restaurant import SafeRestaurantAdapter
from adapters.low_sodium import LowSodiumAdapter
from adapters.food_safety import FoodSafetyAdapter
from adapters.model_restaurant import ModelRestaurantAdapter
from enrich.geocoder import geocode_batch
from enrich.classifier import classify
from storage.raw_archiver import archive_raw

load_dotenv()

PROVINCES = [
    '서울특별시','부산광역시','대구광역시','인천광역시','광주광역시',
    '대전광역시','울산광역시','세종특별자치시','경기도','강원특별자치도',
    '충청북도','충청남도','전북특별자치도','전라남도','경상북도',
    '경상남도','제주특별자치도',
]

# ✅ [v4.2] 시도 별칭 사전 (dedup_key 정규화)
SIDO_ALIAS = {
    '서울':'서울특별시','서울시':'서울특별시',
    '부산':'부산광역시','부산시':'부산광역시',
    '대구':'대구광역시','대구시':'대구광역시',
    '인천':'인천광역시','인천시':'인천광역시',
    '대전':'대전광역시','대전시':'대전광역시',
    '울산':'울산광역시','울산시':'울산광역시',
    '세종':'세종특별자치시','세종시':'세종특별자치시',
    '제주':'제주특별자치도','제주도':'제주특별자치도',
    '경기':'경기도','강원':'강원특별자치도',
    '충북':'충청북도','충남':'충청남도',
    '전북':'전북특별자치도','전남':'전라남도',
    '경북':'경상북도','경남':'경상남도',
}

# pipeline.py 상단에 추가

def norm_phone(p: str) -> str:
    return re.sub(r'\D', '', p or '')


def recover_damaged_names(records: List[CertRecord]) -> int:
    """교차 소스 손상 상호명 복구 (API 호출 0회 — 원본 레이어 활용)"""
    addr_map, phone_map = {}, {}

    # 1패스: 정상 이름으로 룩업 테이블 구축
    for r in records:
        if not is_valid_name(r.raw_name):
            continue
        addr_map.setdefault(norm_addr(r.raw_address), r.raw_name)
        np_ = norm_phone(r.phone)
        if len(np_) >= 9:                      # 마스킹(010-****) 제외
            phone_map.setdefault(np_, r.raw_name)

    # 2패스: 손상 이름 복구
    recovered = 0
    for r in records:
        if is_valid_name(r.raw_name):
            continue
        clean = None
        np_ = norm_phone(r.phone)
        if len(np_) >= 9 and np_ in phone_map:
            clean = phone_map[np_]             # 1순위: 전화번호 일치
        elif norm_addr(r.raw_address) in addr_map:
            clean = addr_map[norm_addr(r.raw_address)]  # 2순위: 주소 일치
        if clean:
            print(f"   🔧 복구: '{r.raw_name}' → '{clean}'")
            r.raw_name = clean
            recovered += 1
    return recovered


def norm_name(name: str) -> str:
    return re.sub(r'[^가-힣a-zA-Z0-9]', '', str(name or '')).lower()


def norm_addr(addr: str) -> str:
    """괄호 제거 + 시도 표준화 + 공백 제거 (대소문자 무관)"""
    s = re.sub(r'\([^)]*\)', '', addr or '')
    toks = s.split()
    if toks and toks[0] in SIDO_ALIAS:
        toks[0] = SIDO_ALIAS[toks[0]]
    return ''.join(toks).lower()


def is_valid_name(name: str) -> bool:
    if not name: return False
    s = name.strip()
    if len(s) < 2: return False
    if '?' in s or '？' in s or '\ufffd' in s: return False
    if s in ('식당', '카페', '음식점', '맛집'): return False
    return True


def make_dedup_key(r: CertRecord) -> str:
    name = norm_name(r.raw_name)
    # ✅ 지오코딩 후 road_name 우선 (정규화된 도로명 주소)
    primary = r.road_name or r.raw_address
    if primary and len(primary.split()) >= 3:
        return f"{name}|{norm_addr(primary)}"
    return f"{name}|{(r.province or '').strip()}|{(r.municipality or '').strip()}"


def merge_cert_types(records: List[CertRecord]) -> List[CertRecord]:
    merged: Dict[str, CertRecord] = {}
    for r in records:
        if not r.is_valid(): continue
        key = make_dedup_key(r)
        if not key or key == '||': continue
        r.dedup_key = key
        if key in merged:
            ex = merged[key]
            old = set((ex.cert_type or '').split(','))
            ex.cert_type = ','.join(sorted(old | set((r.cert_type or '').split(','))))
            if is_valid_name(r.raw_name) and (
                not is_valid_name(ex.raw_name) or len(r.raw_name) > len(ex.raw_name or '')
            ):
                ex.raw_name = r.raw_name
            if not ex.phone and r.phone: ex.phone = r.phone
            if not ex.biz_type and r.biz_type: ex.biz_type = r.biz_type
            if not ex.main_menu and r.main_menu: ex.main_menu = r.main_menu
            if not ex.designated_at and r.designated_at: ex.designated_at = r.designated_at
            if not ex.lat and r.lat: ex.lat, ex.lng = r.lat, r.lng
            if not ex.province and r.province: ex.province = r.province
            if not ex.municipality and r.municipality: ex.municipality = r.municipality
            if not ex.emd and r.emd: ex.emd = r.emd
            if not ex.road_name and r.road_name: ex.road_name = r.road_name
            if not ex.lot_num and r.lot_num: ex.lot_num = r.lot_num
            if len(r.raw_address or '') > len(ex.raw_address or ''):
                ex.raw_address = r.raw_address
        else:
            merged[key] = r
    return list(merged.values())


def _safe_collect(adapter, province, label):
    try:
        if label != 'model_restaurant':
            api_key = getattr(adapter, 'api_key', None) or getattr(adapter, 'service_key', None)
            if not api_key:
                print(f"⚠️ [{label}] API 키 미설정 — 수집 스킵")
                return []
        return adapter.collect(province)
    except Exception as e:
        print(f"⚠️ [{label}] 수집 실패(격리): {type(e).__name__}: {e}")
        return []


def collect_all(province: str = None) -> List[CertRecord]:
    adapters = [
        ('safe_restaurant', SafeRestaurantAdapter(os.getenv('MAFRA_API_KEY', ''))),
        ('low_sodium', LowSodiumAdapter(os.getenv('DATA_GO_KR_KEY', ''))),
        ('food_safety', FoodSafetyAdapter(
            os.getenv('FOODSAFETY_KOREA_KEY', '') or os.getenv('DATA_GO_KR_KEY', ''))),
        ('model_restaurant', ModelRestaurantAdapter()),
    ]
    all_records = []
    print(f"\n🚀 수집 시작 (province={province or '전국'})...")
    for label, adapter in adapters:
        all_records.extend(_safe_collect(adapter, province, label))
        archive_raw(label, getattr(adapter, 'raw_rows', []), province)
    return all_records


def run_pipeline(province: str = None, output: str = "out/certifications.csv"):
    raw_records = collect_all(province)
    print(f"📊 원본 수집: {len(raw_records)}건")

     # ✅ [신규] 병합 "전"에 손상명 복구 (dedup_key 품질 보장)
    recovered = recover_damaged_names(raw_records)
    print(f"🔧 교차 소스 상호명 복구: {recovered}건")
    
    merged = merge_cert_types(raw_records)
    print(f"🔀 배지 병합 후: {len(merged)}건 (중복 제거: {len(raw_records) - len(merged)}건)")

    before = len(merged)
    merged = [r for r in merged if is_valid_name(r.raw_name)]
    removed = before - len(merged)
    print(f"🔍 상호명 검증: {before} → {len(merged)}건 ({removed}건 필터링)")
    if before > 0 and removed / before > 0.5:
        print("🚨 경고: 상호명 필터가 절반 이상 제거 — 검증 로직 오작동 의심!")

    badge_stats = Counter()
    for r in merged:
        for b in (r.cert_type or '').split(','):
            if b: badge_stats[b] += 1
    print("📊 배지별 분포:")
    for badge, count in badge_stats.items():
        print(f"   {badge}: {count}건")

    # 7차원 태깅
    for r in merged:
        tags = classify(r.main_menu or r.raw_name, r.biz_type)
        r.cuisine_tags = tags['cuisine_tags']
        r.nutrient_tags = tags['nutrient_tags']
        r.cooking_tags = tags['cooking_tags']
        r.ingredient_tags = tags['ingredient_tags']
        r.regional_tags = tags['regional_tags']
        r.allergen_tags = tags['allergen_tags']
        r.occasion_tags = tags['occasion_tags']

    enriched = geocode_batch(merged)

    # ✅ 지오코딩 후 dedup_key 재생성 + 중복 제거
    for r in enriched:
        r.dedup_key = make_dedup_key(r)

    dedup_dict = {}
    for r in enriched:
        if r.dedup_key:
            dedup_dict[r.dedup_key] = r

    before = len(enriched)
    enriched = list(dedup_dict.values())
    print(f"🔍 중복 제거: {before} → {len(enriched)}건 ({before - len(enriched)}건 제거)")

    import pandas as pd
    df = pd.DataFrame([{
        'source': r.source, 'cert_type': r.cert_type,
        'store_name': r.raw_name, 'address': r.raw_address,
        'province': r.province, 'municipality': r.municipality, 'emd': r.emd,
        'road_name': r.road_name, 'lot_num': r.lot_num,
        'phone': r.phone, 'biz_type': r.biz_type, 'main_menu': r.main_menu,
        'designated_at': r.designated_at, 'lat': r.lat, 'lng': r.lng,
        'cuisine_tags': '|'.join(r.cuisine_tags or []),
        'nutrient_tags': '|'.join(r.nutrient_tags or []),
        'cooking_tags': '|'.join(r.cooking_tags or []),
        'ingredient_tags': '|'.join(r.ingredient_tags or []),
        'regional_tags': '|'.join(r.regional_tags or []),
        'allergen_tags': '|'.join(r.allergen_tags or []),
        'occasion_tags': '|'.join(r.occasion_tags or []),
        'dedup_key': r.dedup_key,
    } for r in enriched])

    os.makedirs('out', exist_ok=True)
    df.to_csv(output, index=False, encoding='utf-8-sig')
    print(f"\n📄 저장: {output} ({len(df)}건)")

    multi = [r for r in enriched if r.cert_type and ',' in r.cert_type]
    if multi:
        print(f"\n✨ 다중 배지 획득 가게 ({len(multi)}건) 샘플:")
        for r in multi[:5]:
            print(f"   🏆 {r.raw_name}: [{r.cert_type}]")


if __name__ == "__main__":
    run_pipeline(province='제주특별자치도', output='out/제주특별자치도.csv')
    # 전국 17개 시도 순회 (심야 실행 권장)
    # for province in PROVINCES:
    #     run_pipeline(province=province, output=f'out/{province}.csv')