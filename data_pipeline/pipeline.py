import os, sys, re, time, glob, json, shutil, hashlib
from typing import List, Dict, Optional
from collections import Counter
from datetime import datetime
from dotenv import load_dotenv
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from adapters.base import CertRecord, SIDO_ALIAS, extract_emd_from_addr
from adapters.safe_restaurant import SafeRestaurantAdapter
from adapters.low_sodium import LowSodiumAdapter
from adapters.food_safety import FoodSafetyAdapter
from adapters.model_restaurant import ModelRestaurantAdapter
from enrich.geocoder import geocode_batch, detect_coordinate_anomalies
from enrich.classifier import classify
from enrich.official_enricher import OfficialEnricher
from storage.raw_archiver import archive_raw

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE_DIR, 'out')
DONE_DIR = os.path.join(OUT_DIR, '.done')
os.makedirs(OUT_DIR, exist_ok=True)
os.makedirs(DONE_DIR, exist_ok=True)

PROVINCES = [
    "서울특별시", "부산광역시", "대구광역시", "인천광역시",
    "대전광역시", "울산광역시", "세종특별자치시",
    "경기도", "강원특별자치도",
    "충청북도", "충청남도", "전북특별자치도",
    "전남광주통합특별시",
    "경상북도", "경상남도", "제주특별자치도",
]

def _split_menus(record: CertRecord):
    """main_menu를 1개로 분리, 나머지는 menus[]에 저장"""
    if not record.main_menu:
        return

    raw = record.main_menu
    parts = re.split(r'[,/、]+', raw)
    parts = [p.strip() for p in parts if p.strip() and len(p.strip()) >= 2]

    if not parts:
        return

    record.main_menu = parts[0]
    if len(parts) > 1:
        existing = set(record.menus or [])
        for p in parts[1:6]:
            existing.add(p)
        record.menus = sorted(existing)

def _extract_menu_from_name(name: str) -> Optional[str]:
    """상호명에서 메뉴 추출 (예: '올래국수' → '국수', '흑돼지맛집' → '흑돼지')"""
    if not name:
        return None
    
    # 일반적인 메뉴 패턴
    patterns = [
        r'(.+?)전문점',
        r'(.+?)맛집',
        r'(.+?)구이',
        r'(.+?)찌개',
        r'(.+?)탕',
        r'(.+?)국수',
        r'(.+?)냉면',
        r'(.+?)밥',
        r'(.+?)집',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, name)
        if match:
            extracted = match.group(1).strip()
            # 너무 짧거나 일반 단어면 스킵
            if len(extracted) >= 2 and extracted not in ('맛', '참', '진', '옛', '본'):
                return extracted
    
    return None

# ───────────────────────────────────────────────────────────
# ✅ 대표메뉴 복구 — 공식 소스만 (추측 완전 제거)
# ───────────────────────────────────────────────────────────
# pipeline.py — _recover_main_menu 함수 수정

def _recover_main_menu(records: List[CertRecord]) -> int:
    """대표메뉴 복구 — 교차 소스만 유지"""
    recovered_cross = 0

    for r in records:
        if r.main_menu and not r.main_menu_source:
            r.main_menu_source = 'original'

    # 1단계: 교차 소스 메뉴 복구 (유지)
    menu_map: Dict[str, str] = {}
    for r in records:
        if r.main_menu and r.raw_name and r.raw_address:
            key = (r.raw_name.strip(), r.raw_address.strip()[:20])
            if key not in menu_map or len(r.main_menu) > len(menu_map[key]):
                menu_map[key] = r.main_menu

    for r in records:
        if not r.main_menu and r.raw_name and r.raw_address:
            key = (r.raw_name.strip(), r.raw_address.strip()[:20])
            if key in menu_map:
                r.main_menu = menu_map[key]
                r.main_menu_source = 'cross_source'
                recovered_cross += 1

    # ❌ 2단계 _extract_menu_from_name 완전 삭제 (쓰레기 양산 주범)
    # 메뉴를 못 찾으면 NULL 유지 (정직한 NULL 원칙)

    print(f"🍽️ 대표메뉴 복구: 교차 {recovered_cross}건")
    return recovered_cross


# ───────────────────────────────────────────────────────────
# 기존 유틸리티 함수
# ───────────────────────────────────────────────────────────
def norm_phone(p: str) -> str:
    return re.sub(r"\D", "", p or "")


def recover_damaged_names(records: List[CertRecord]) -> int:
    addr_map, phone_map = {}, {}
    for r in records:
        if not is_valid_name(r.raw_name):
            continue
        addr_map.setdefault(norm_addr(r.raw_address), r.raw_name)
        np_ = norm_phone(r.phone)
        if len(np_) >= 9:
            phone_map.setdefault(np_, r.raw_name)

    recovered = 0
    for r in records:
        if is_valid_name(r.raw_name):
            continue
        clean = None
        np_ = norm_phone(r.phone)
        if len(np_) >= 9 and np_ in phone_map:
            clean = phone_map[np_]
        elif norm_addr(r.raw_address) in addr_map:
            clean = addr_map[norm_addr(r.raw_address)]
        if clean:
            print(f"   🔧 복구: '{r.raw_name}' → '{clean}'")
            r.raw_name = clean
            recovered += 1
    return recovered


def norm_name(name: str) -> str:
    return re.sub(r"[^가-힣a-zA-Z0-9]", "", str(name or "")).lower()


def norm_addr(addr: str) -> str:
    s = re.sub(r"\([^)]*\)", "", addr or "")
    toks = s.split()
    if toks and toks[0] in SIDO_ALIAS:
        toks[0] = SIDO_ALIAS[toks[0]]
    return "".join(toks).lower()


def is_valid_name(name: str) -> bool:
    if not name:
        return False
    s = name.strip()
    if len(s) < 2:
        return False
    if "?" in s or "\ufffd" in s:
        return False
    if s in ("식당", "카페", "음식점", "맛집"):
        return False
    return True


def make_dedup_key(r: CertRecord) -> str:
    """✅ 고도화된 dedup_key — 체인점/푸드코트 구분 강화"""
    name = norm_name(r.raw_name)
    primary = r.road_name or r.raw_address
    
    if primary and len(primary.split()) >= 3:
        addr_key = norm_addr(primary)
    else:
        addr_key = f"{(r.province or '').strip()}|{(r.municipality or '').strip()}"

    # ✅ 상세 주소 해시 추가 (같은 건물 다른 층 구분)
    detail = r.lot_num or r.road_name or ''
    detail_hash = hashlib.md5(detail.encode()).hexdigest()[:8]
    
    # 전화번호 포함 (동일 상호 다른 지점 구분)
    phone_norm = norm_phone(r.phone) if r.phone else ''
    if phone_norm and len(phone_norm) >= 9:
        return f"{name}|{addr_key}|{phone_norm}"
    
    # 전화번호 없으면 상세 주소 해시 사용
    return f"{name}|{addr_key}|{detail_hash}"


def merge_cert_types(records: List[CertRecord]) -> List[CertRecord]:
    merged: Dict[str, CertRecord] = {}
    for r in records:
        if not r.is_valid():
            continue
        key = make_dedup_key(r)
        if not key or key == "||":
            continue
        r.dedup_key = key
        if key in merged:
            ex = merged[key]
            old = set((ex.cert_type or "").split(","))
            ex.cert_type = ",".join(sorted(old | set((r.cert_type or "").split(","))))
            if is_valid_name(r.raw_name) and (
                not is_valid_name(ex.raw_name)
                or len(r.raw_name) > len(ex.raw_name or "")
            ):
                ex.raw_name = r.raw_name
            if not ex.phone and r.phone: ex.phone = r.phone
            if not ex.biz_type and r.biz_type: ex.biz_type = r.biz_type

            if r.main_menu and (not ex.main_menu
                                or len(r.main_menu) > len(ex.main_menu or '')):
                ex.main_menu = r.main_menu
                priority = {'public_api': 4, 'cross_source': 3, 'naver_api': 2, 'kakao_api': 2}
                r_prio = priority.get(r.main_menu_source or 'public_api', 0)
                e_prio = priority.get(ex.main_menu_source or 'public_api', 0)
                if r_prio >= e_prio:
                    ex.main_menu_source = r.main_menu_source

            if r.menus:
                ex.menus = list(set((ex.menus or []) + r.menus))

            if not ex.designated_at and r.designated_at: ex.designated_at = r.designated_at
            if not ex.lat and r.lat: ex.lat, ex.lng = r.lat, r.lng
            if not ex.province and r.province: ex.province = r.province
            if not ex.municipality and r.municipality: ex.municipality = r.municipality
            if not ex.emd and r.emd: ex.emd = r.emd
            if not ex.road_name and r.road_name: ex.road_name = r.road_name
            if not ex.lot_num and r.lot_num: ex.lot_num = r.lot_num
            if len(r.raw_address or "") > len(ex.raw_address or ""):
                ex.raw_address = r.raw_address
        else:
            merged[key] = r
    return list(merged.values())


def _safe_collect(adapter, province, label):
    try:
        if label != "model_restaurant":
            api_key = getattr(adapter, "api_key", None) or getattr(adapter, "service_key", None)
            if not api_key:
                print(f"⚠️ [{label}] API 키 미설정 — 수집 스킵")
                return []
        return adapter.collect(province)
    except Exception as e:
        print(f"⚠️ [{label}] 수집 실패(격리): {type(e).__name__}: {e}")
        return []


def collect_all(province: str = None, snapshot_food_safety: bool = False) -> List[CertRecord]:
    adapters = [
        ("safe_restaurant", SafeRestaurantAdapter(os.getenv("MAFRA_API_KEY", ""))),
        ("low_sodium", LowSodiumAdapter(os.getenv("DATA_GO_KR_KEY", ""))),
        ("food_safety", FoodSafetyAdapter(
            os.getenv("FOODSAFETY_KOREA_KEY", ""),
            os.getenv("DATA_GO_KR_KEY", ""),
            snapshot_first=snapshot_food_safety,
        )),
        ("model_restaurant", ModelRestaurantAdapter()),
    ]
    all_records = []
    print(f"\n🚀 수집 시작 (province={province or '전국'})...")
    for label, adapter in adapters:
        all_records.extend(_safe_collect(adapter, province, label))
        archive_raw(label, getattr(adapter, "raw_rows", []), province)
    return all_records


def run_pipeline(province: str = None, output: str = None,
                 snapshot_food_safety: bool = False) -> List[CertRecord]:
    """✅ 수정: 레코드 리스트 반환 (마커 저장용)"""
    if output is None:
        tag = province or 'nationwide'
        output = os.path.join(OUT_DIR, f"{tag}.csv")

    raw_records = collect_all(province, snapshot_food_safety=snapshot_food_safety)
    print(f"📊 원본 수집: {len(raw_records)}건")

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

    _recover_main_menu(merged)

    badge_stats = Counter()
    for r in merged:
        for b in (r.cert_type or "").split(","):
            if b:
                badge_stats[b] += 1
    print("📊 배지별 분포:")
    for badge, count in badge_stats.items():
        print(f"   {badge}: {count}건")

    source_stats = Counter(r.main_menu_source or 'unknown' for r in merged)
    print("📊 대표메뉴 source 분포:")
    for src, count in source_stats.items():
        print(f"   {src}: {count}건")
        
    for r in merged:
        _split_menus(r)    

    for r in merged:
        tags = classify(r.main_menu or r.raw_name or '', r.biz_type or '')
        r.cuisine_tags = tags["cuisine_tags"]
        r.nutrient_tags = tags["nutrient_tags"]
        r.cooking_tags = tags["cooking_tags"]
        r.ingredient_tags = tags["ingredient_tags"]
        r.regional_tags = tags["regional_tags"]
        r.allergen_tags = tags["allergen_tags"]
        r.occasion_tags = tags["occasion_tags"]

    enriched = geocode_batch(merged)

    detect_coordinate_anomalies(enriched)

    enricher = OfficialEnricher()
    enriched = enricher.enrich_batch(enriched)

    before = len(enriched)
    enriched = [r for r in enriched if r.is_active]
    print(f"👻 최종 유령가게 제거: {before} → {len(enriched)}건 ({before - len(enriched)}건)")

    for r in enriched:
        if not r.emd or r.emd in ('', None):
            lot_emd = extract_emd_from_addr(r.lot_num) if r.lot_num else ''
            orig_emd = extract_emd_from_addr(r.raw_address) if r.raw_address else ''
            r.emd = lot_emd or orig_emd or ''
        r.dedup_key = make_dedup_key(r)

    dedup_dict = {}
    for r in enriched:
        if r.dedup_key:
            dedup_dict[r.dedup_key] = r

    before = len(enriched)
    enriched = list(dedup_dict.values())
    print(f"🔍 중복 제거: {before} → {len(enriched)}건 ({before - len(enriched)}건 제거)")

    df = pd.DataFrame([{
        "source": r.source, "cert_type": r.cert_type,
        "store_name": r.raw_name, "address": r.raw_address,
        "province": r.province, "municipality": r.municipality, "emd": r.emd,
        "road_name": r.road_name, "lot_num": r.lot_num,
        "phone": r.phone, "biz_type": r.biz_type,
        "main_menu": r.main_menu,
        "main_menu_source": r.main_menu_source or '',
        "menus": "|".join(r.menus or []),
        "menus_source": r.menus_source or '',
        "ghost_suspicion_score": r.ghost_suspicion_score,
        "coord_confidence": r.coord_confidence or '',
        "designated_at": r.designated_at, "lat": r.lat, "lng": r.lng,
        "cuisine_tags": "|".join(r.cuisine_tags or []),
        "nutrient_tags": "|".join(r.nutrient_tags or []),
        "cooking_tags": "|".join(r.cooking_tags or []),
        "ingredient_tags": "|".join(r.ingredient_tags or []),
        "regional_tags": "|".join(r.regional_tags or []),
        "allergen_tags": "|".join(r.allergen_tags or []),
        "occasion_tags": "|".join(r.occasion_tags or []),
        "quality_score": calculate_quality_score(r),
        "dedup_key": r.dedup_key,
    } for r in enriched])

    df.to_csv(output, index=False, encoding="utf-8-sig")
    print(f"\n📄 저장: {output} ({len(df)}건)")

    menu_found = df[df['main_menu'].notna() & (df['main_menu'] != '')]
    print(f"🍽️ 대표메뉴 발견율: {len(menu_found)}/{len(df)} ({len(menu_found)/len(df)*100:.1f}%)")

    multi = [r for r in enriched if r.cert_type and "," in r.cert_type]
    if multi:
        print(f"\n✨ 다중 배지 획득 가게 ({len(multi)}건) 샘플:")
        for r in multi[:5]:
            print(f"   🏆 {r.raw_name}: [{r.cert_type}]")
    
    # ✅ 레코드 리스트 반환 (마커 저장용)
    return enriched


def calculate_quality_score(r: CertRecord) -> int:
    """레코드별 데이터 품질 점수 계산"""
    score = 0

    if r.raw_name: score += 10
    if r.raw_address: score += 10
    if r.phone: score += 10
    if r.main_menu: score += 10

    if r.province: score += 5
    if r.municipality: score += 5
    if r.emd: score += 5
    if r.road_name: score += 5
    if r.lot_num: score += 5

    if r.lat and r.lng: score += 15

    menu_source_score = {
        'public_api': 10, 'cross_source': 8,
        'naver_api': 6, 'kakao_api': 6,
    }
    score += menu_source_score.get(r.main_menu_source or '', 0)

    if r.cert_type:
        cert_count = len(r.cert_type.split(','))
        score += min(cert_count * 3, 10)

    return min(score, 100)


# ───────────────────────────────────────────────────────────
# ✅ 마커 Freshness 검증 (신규)
# ───────────────────────────────────────────────────────────
def _check_marker_freshness(marker_path: str) -> bool:
    """마커 파일의 데이터 신선도 검증 (24시간 이내만 유효)"""
    if not os.path.exists(marker_path):
        return False
    
    try:
        with open(marker_path, 'r', encoding='utf-8') as f:
            content = f.read().strip()
            
            # 구버전 마커 (단순 텍스트) → 무효
            if not content.startswith('{'):
                print(f"   ⚠️ 구버전 마커 감지 — 재실행 필요")
                return False
            
            meta = json.loads(content)
            completed_at = datetime.fromisoformat(meta['completed_at'])
            hours_ago = (datetime.now() - completed_at).total_seconds() / 3600
            
            # 24시간 이내만 유효
            if hours_ago > 24:
                print(f"   ⚠️ 마커 만료 ({hours_ago:.1f}시간 전) — 재실행 필요")
                return False
            
            return True
    except Exception as e:
        print(f"   ⚠️ 마커 검증 실패: {e}")
        return False


def _save_marker(marker_path: str, record_count: int):
    """新版 마커 저장 (메타데이터 포함)"""
    meta = {
        'completed_at': datetime.now().isoformat(),
        'record_count': record_count,
    }
    with open(marker_path, 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def run_national():
    print("\n" + "=" * 60)
    print("📸 Step 0: food_safety 전국 스캔 (1회)")
    print("=" * 60)
    fs = FoodSafetyAdapter(
        os.getenv('FOODSAFETY_KOREA_KEY', ''),
        os.getenv('DATA_GO_KR_KEY', '')
    )
    fs.collect(province=None)

    snapshot_path = os.path.join(fs.RAW_DIR, 'nationwide_latest.json')
    if not os.path.exists(snapshot_path):
        print("⚠️ [경고] nationwide_latest.json 미생성 — 리플레이 원본 사용")
        files = sorted(glob.glob(os.path.join(fs.RAW_DIR, '*.json')),
                       key=lambda f: os.path.getsize(f), reverse=True)
        if files:
            shutil.copy(files[0], snapshot_path)
            print(f"   📋 {os.path.basename(files[0])} → nationwide_latest.json 복사")
    else:
        with open(snapshot_path, encoding='utf-8') as f:
            rows = json.load(f)
        print(f"✅ nationwide_latest.json 생성 확인 ({len(rows)}건)")

    print("\n" + "=" * 60)
    print("🚀 Step 1: 시도별 수집 (스냅샷 모드)")
    print("=" * 60)

    for i, p in enumerate(PROVINCES, 1):
        marker = os.path.join(DONE_DIR, f'{p}.done')
        
        # ✅ Freshness 검증 추가
        if _check_marker_freshness(marker):
            print(f"\n⏭️ [{i}/{len(PROVINCES)}] {p} 스킵 (완료됨, 24시간 이내)")
            continue

        print(f"\n{'=' * 60}")
        print(f"🚀 [{i}/{len(PROVINCES)}] {p}")
        print("=" * 60)

        try:
            # ✅ run_pipeline이 레코드 리스트를 반환하도록 수정됨
            records = run_pipeline(
                province=p,
                output=os.path.join(OUT_DIR, f"{p}.csv"),
                snapshot_food_safety=True
            )
            
            # ✅ 새 마커 저장 (레코드 수 포함)
            _save_marker(marker, len(records) if records else 0)
            print(f"✅ {p} 완료 마커 생성 (레코드: {len(records) if records else 0}건)")
        except Exception as e:
            print(f"🚨 {p} 실패: {type(e).__name__}: {e}")
            continue

    print("\n" + "=" * 60)
    print("🎉 전국 수집 완료!")
    print("=" * 60)


if __name__ == "__main__":
    run_national()