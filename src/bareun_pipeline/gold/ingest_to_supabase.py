# data_pipeline/ingest_to_supabase.py
import csv
import glob
import os
import re
import sys
import time

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CHUNK = 5000

TAG_FIELDS = (
    'cuisine_tags',
    'nutrient_tags',
    'cooking_tags',
    'ingredient_tags',
    'regional_tags',
    'allergen_tags',
    'occasion_tags',
)


def _get_sb():
    from supabase import create_client

    url = (os.getenv('SUPABASE_URL') or '').strip()
    key = (os.getenv('SUPABASE_SERVICE_ROLE_KEY') or '').strip()
    if not url or not key:
        raise RuntimeError(
            'SUPABASE_URL 또는 SUPABASE_SERVICE_ROLE_KEY 미설정. ' '.env 파일을 확인하세요.'
        )
    return create_client(url, key)


def _normalize(v):
    v = (v or '').strip()
    return v if v else None


def clean_phone(v):
    v = re.sub(r'\s+', '', v or '')
    return v if v and '****' not in v and not v.startswith('000-') else None


def clean_date(v):
    v = (v or '').strip()
    return v if re.match(r'^\d{4}-\d{2}-\d{2}$', v) else None


def norm_biz(raw):
    raw = raw or ''
    for std, kws in [
        ('한식', ['한식', '힌식']),
        ('일식', ['일식', '회', '초밥', '횟집']),
        ('중식', ['중식', '짜장', '짬']),
        ('양식', ['양식', '경양식', '스테이크', '파스타', '피자']),
        ('분식', ['분식']),
        ('패스트푸드/치킨', ['패스트푸드', '치킨']),
        ('카페', ['카페', '커피']),
        ('뷔페', ['뷔페']),
        ('구이', ['구이', '고기', '고깃집']),
        ('탕/찌개', ['탕', '찌개', '전골', '국밥']),
    ]:
        if raw.startswith(std) or any(k in raw for k in kws):
            return std
    return raw or None


def split_tags(v):
    return [t for t in (v or '').split('|') if t]


def consolidate(rows):
    """동일 dedup_key 행 병합 (배열 정렬로 Write I/O 최소화)"""
    merged = {}
    for row in rows:
        key = row['dedup_key']
        if key in merged:
            base = merged[key]
            base['certs'] = ','.join(
                sorted(set(base['certs'].split(',')) | set(row['certs'].split(',')))
            )

            # ✅ 모든 태그 배열 sorted() 적용
            for f in TAG_FIELDS:
                base[f] = sorted(set(base[f]) | set(row[f]))

            # ✅ menus 배열 sorted() 적용
            new_menu = row.get('main_menu')
            if new_menu and (
                not base.get('main_menu') or len(new_menu) > len(base.get('main_menu') or '')
            ):
                base['main_menu'] = new_menu
                base['main_menu_source'] = row.get('main_menu_source')

            new_menus = row.get('menus') or []
            if new_menus:
                base['menus'] = sorted(set((base.get('menus') or []) + new_menus))

            for f in ('phone', 'biz_type', 'address_road', 'address_lotnum'):
                if not base.get(f) and row.get(f):
                    base[f] = row[f]
        else:
            # ✅ 신규 행도 배열 정렬
            for f in TAG_FIELDS:
                if row.get(f):
                    row[f] = sorted(set(row[f]))
            if row.get('menus'):
                row['menus'] = sorted(set(row['menus']))
            merged[key] = row
    return list(merged.values())


def load_csv(csv_path):
    """단일 CSV 파일 로드 + Gold 적재용 컬럼 변환"""
    rows = []
    with open(csv_path, encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            try:
                lat, lng = float(r['lat']), float(r['lng'])
            except Exception:
                continue
            dk = (r.get('dedup_key') or '').strip()
            if not dk or dk in ('|', '||'):
                continue

            road_raw = (r.get('road_name') or '').strip()
            addr_raw = (r.get('address') or '').strip()
            address_road = road_raw or addr_raw
            address_lotnum = (r.get('lot_num') or '').strip()

            rows.append(
                {
                    'store_name': _normalize(r['store_name']),
                    'province': _normalize(r['province']),
                    'municipality': _normalize(r['municipality']),
                    'emd': _normalize(r.get('emd')),
                    'address_road': _normalize(address_road),
                    'address_lotnum': _normalize(address_lotnum),
                    'main_menu': _normalize(r.get('main_menu')),
                    'main_menu_source': _normalize(r.get('main_menu_source')),
                    'menus': split_tags(r.get('menus')),  # ✅ [신규]
                    'menus_source': _normalize(r.get('menus_source')),  # ✅ [신규]
                    'lat': lat,
                    'lng': lng,
                    'phone': clean_phone(r.get('phone')),
                    'biz_type': norm_biz(r.get('biz_type')),
                    'certs': r['cert_type'],
                    'data_source': _normalize(r.get('source')),
                    'designated_at': clean_date(r.get('designated_at')),
                    'quality_score': int(float(r.get('quality_score', 0) or 0)),
                    'dedup_key': dk,
                    'cuisine_tags': split_tags(r.get('cuisine_tags')),
                    'nutrient_tags': split_tags(r.get('nutrient_tags')),
                    'cooking_tags': split_tags(r.get('cooking_tags')),
                    'ingredient_tags': split_tags(r.get('ingredient_tags')),
                    'regional_tags': split_tags(r.get('regional_tags')),
                    'allergen_tags': split_tags(r.get('allergen_tags')),
                    'occasion_tags': split_tags(r.get('occasion_tags')),
                }
            )
    return rows


def main(csv_pattern=None, single_file=None, analyze_after=True):
    sb = _get_sb()

    if single_file:
        if not os.path.isabs(single_file):
            candidate1 = os.path.abspath(single_file)
            candidate2 = os.path.join(BASE_DIR, single_file)
            if os.path.exists(candidate1):
                single_file = candidate1
            elif os.path.exists(candidate2):
                single_file = candidate2
            else:
                raise FileNotFoundError(
                    f'파일을 찾을 수 없음: {single_file}\n'
                    f'  시도 1 (CWD): {candidate1}\n'
                    f'  시도 2 (BASE_DIR): {candidate2}'
                )

    if csv_pattern is None:
        csv_pattern = os.path.join(BASE_DIR, 'out', '*.csv')

    all_rows = []
    if single_file:
        print(f'📥 단일 파일 적재: {single_file}')
        all_rows = load_csv(single_file)
    else:
        files = sorted(glob.glob(csv_pattern))
        if not files:
            print(f'⚠️ CSV 파일 없음: {csv_pattern}')
            return
        print(f'📥 전국 CSV 통합 적재: {len(files)}개 파일')
        for f in files:
            rows = load_csv(f)
            print(f'   {os.path.basename(f)}: {len(rows)}건')
            all_rows.extend(rows)

    if not all_rows:
        print('⚠️ 적재할 데이터 없음')
        return

    before = len(all_rows)
    all_rows = consolidate(all_rows)
    print(f'📥 적재 대상: {len(all_rows)}건 (중복 통합: {before - len(all_rows)}건)')

    total = 0
    for i in range(0, len(all_rows), CHUNK):
        chunk = all_rows[i : i + CHUNK]
        for attempt in range(3):
            try:
                res = sb.rpc('bulk_upsert_restaurants', {'payload': chunk}).execute()
                raw_affected = res.data
                if isinstance(raw_affected, (int, float)) or (
                    isinstance(raw_affected, str) and raw_affected.isdigit()
                ):
                    total += int(raw_affected)
                print(
                    f'✅ bulk upsert {min(i + CHUNK, len(all_rows))}/{len(all_rows)} (affected {raw_affected})'
                )
                break
            except Exception as e:
                if attempt < 2:
                    wait = 2**attempt
                    print(f'   ↻ 재시도 {attempt + 1}/3 (대기 {wait}초): {e}')
                    time.sleep(wait)
                else:
                    raise

    print(f'🎉 총 affected: {total}건')

    if analyze_after:
        try:
            sb.rpc('analyze_restaurants').execute()
            print('📊 ANALYZE 완료 (GiST/GiN 인덱스 통계 갱신)')
        except Exception as e:
            print(f'⚠️ ANALYZE 실패 (무시): {e}')


if __name__ == '__main__':
    if len(sys.argv) > 1:
        main(single_file=sys.argv[1])
    else:
        main()
