# data_pipeline/ingest_to_supabase.py
import os, re, csv, glob
from dotenv import load_dotenv
from supabase import create_client
import time

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

sb = create_client(os.getenv('SUPABASE_URL').strip(),
                   os.getenv('SUPABASE_SERVICE_ROLE_KEY').strip())
CHUNK = 5000

TAG_FIELDS = ('cuisine_tags', 'nutrient_tags', 'cooking_tags', 'ingredient_tags',
              'regional_tags', 'allergen_tags', 'occasion_tags')


def clean_phone(v):
    v = re.sub(r'\s+', '', v or '')
    return v if v and '****' not in v and not v.startswith('000-') else None


def clean_date(v):
    v = (v or '').strip()
    return v if re.match(r'^\d{4}-\d{2}-\d{2}$', v) else None


def norm_biz(raw):
    raw = raw or ''
    for std, kws in [('한식', ['한식']), ('일식', ['일식', '회', '초밥']), ('중식', ['중식', '짜장', '짬']),
                     ('양식', ['양식', '스테이크', '파스타', '피자']), ('분식', ['분식']),
                     ('패스트푸드/치킨', ['패스트푸드', '치킨']), ('뷔페', ['뷔페'])]:
        if raw.startswith(std) or any(k in raw for k in kws):
            return std
    return raw or None


def split_tags(v):
    return [t for t in (v or '').split('|') if t]


def consolidate(rows):
    """동일 dedup_key 행 병합 (21000 에러 방어)"""
    merged = {}
    for row in rows:
        key = row['dedup_key']
        if key in merged:
            base = merged[key]
            base['certs'] = ','.join(sorted(
                set(base['certs'].split(',')) | set(row['certs'].split(','))))
            for f in TAG_FIELDS:
                base[f] = sorted(set(base[f]) | set(row[f]))
            for f in ('phone', 'main_menu', 'biz_type'):
                if not base.get(f) and row.get(f):
                    base[f] = row[f]
        else:
            merged[key] = row
    return list(merged.values())


def load_csv(csv_path):
    """단일 CSV 파일 로드"""
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
            rows.append({
                'store_name': (r['store_name'] or '').strip(),
                'province': r['province'], 'municipality': r['municipality'],
                'emd': r.get('emd'), 'road_name': r.get('road_name'), 'lot_num': r.get('lot_num'),
                'main_menu': r.get('main_menu'), 'lat': lat, 'lng': lng,
                'phone': clean_phone(r.get('phone')),
                'biz_type': norm_biz(r.get('biz_type')),
                'certs': r['cert_type'],
                'data_source': r.get('source'),
                'designated_at': clean_date(r.get('designated_at')),
                'dedup_key': dk,
                'cuisine_tags': split_tags(r.get('cuisine_tags')),
                'nutrient_tags': split_tags(r.get('nutrient_tags')),
                'cooking_tags': split_tags(r.get('cooking_tags')),
                'ingredient_tags': split_tags(r.get('ingredient_tags')),
                'regional_tags': split_tags(r.get('regional_tags')),
                'allergen_tags': split_tags(r.get('allergen_tags')),
                'occasion_tags': split_tags(r.get('occasion_tags')),
            })
    return rows


def main(csv_pattern=None, single_file=None, analyze_after=True):
    if csv_pattern is None:
        csv_pattern = os.path.join(BASE_DIR, 'out', '*.csv')
    """전국 CSV 통합 적재 또는 단일 파일 적재"""
    all_rows = []
    
    if single_file:
        print(f"📥 단일 파일 적재: {single_file}")
        all_rows = load_csv(single_file)
    else:
        files = sorted(glob.glob(csv_pattern))
        print(f"📥 전국 CSV 통합 적재: {len(files)}개 파일")
        for f in files:
            rows = load_csv(f)
            print(f"   {os.path.basename(f)}: {len(rows)}건")
            all_rows.extend(rows)

    before = len(all_rows)
    all_rows = consolidate(all_rows)
    print(f"📥 적재 대상: {len(all_rows)}건 (중복 통합: {before - len(all_rows)}건)")

    total = 0
    for i in range(0, len(all_rows), CHUNK):
        chunk = all_rows[i:i + CHUNK]
        
        # ✅ 네트워크 재시도 (지수 백오프)
        for attempt in range(3):
            try:
                res = sb.rpc('bulk_upsert_restaurants', {'payload': chunk}).execute()
                total += int(res.data or 0)
                print(f"✅ bulk upsert {min(i + CHUNK, len(all_rows))}/{len(all_rows)} (affected {res.data})")
                break
            except Exception as e:
                if attempt < 2:
                    wait = 2 ** attempt
                    print(f"   ↻ 재시도 {attempt+1}/3 (대기 {wait}초): {e}")
                    time.sleep(wait)
                else:
                    raise
    
    print(f"🎉 총 affected: {total}건")
    
    # ✅ 적재 후 통계 갱신
    if analyze_after:
        try:
            sb.rpc('analyze_restaurants').execute()
            print("📊 ANALYZE 완료 (GiST/GiN 인덱스 통계 갱신)")
        except Exception as e:
            print(f"⚠️ ANALYZE 실패: {e}")

if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1:
        # 단일 파일 적재: python ingest_to_supabase.py out/제주특별자치도.csv
        main(single_file=sys.argv[1])
    else:
        # 전국 통합 적재: python ingest_to_supabase.py
        main()