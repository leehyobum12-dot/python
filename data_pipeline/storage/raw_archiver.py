# data_pipeline/storage/raw_archiver.py
import os, sys, json, glob
from datetime import datetime

# ✅ 직접 실행 시에도 adapters 패키지를 찾도록 경로 보정
_PIPELINE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PIPELINE_ROOT not in sys.path:
    sys.path.insert(0, _PIPELINE_ROOT)

from adapters.base import canon_province, match_prefixes

RAW_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'raw')
RETENTION = 3

# ✅ [PIPA] 개인정보(PII) 필드 — 저장 전 비식별화
PII_FIELDS = {
    'RELAX_RSTRNT_REPRESENT',   # safe_restaurant: 대표자 실명 🔴
    'PRSDNT_NM',                # food_safety: 대표자명
    'WRKR_REG_NO',              # food_safety: 종사자 등록번호
}
PII_MASK = '[비식별화]'


def _redact_pii(rows: list) -> list:
    """개인정보 필드를 마스킹 (원본 저장 전 반드시 호출)"""
    for row in rows:
        if isinstance(row, dict):
            for key in PII_FIELDS:
                if key in row and row[key]:
                    row[key] = PII_MASK
    return rows


def _filter_rows(source: str, rows: list, province: str) -> list:
    """지역별 필터링"""
    if not province:
        return rows
    prefixes = match_prefixes(province)
    out = []
    for r in rows:
        if source == 'low_sodium':
            addr = r.get('SITE_ADDR') or ''
            if any(p in addr for p in prefixes):
                out.append(r)
        elif source == 'food_safety':
            addr = r.get('ADDR') or r.get('SITE_ADDR') or ''
            if addr.startswith(prefixes):
                out.append(r)
        elif source == 'safe_restaurant':
            if canon_province(r.get('RELAX_SI_NM')) == canon_province(province):
                out.append(r)
        else:  # model_restaurant: collect 단계에서 이미 필터링됨
            out.append(r)
    return out


def _prune(source_dir: str, keep: int = RETENTION):
    files = sorted(glob.glob(os.path.join(source_dir, '*_20*.json')),
                   key=os.path.getmtime, reverse=True)
    for old in files[keep:]:
        try:
            os.remove(old)
        except OSError:
            pass


def archive_raw(source: str, rows: list, province: str = None):
    if not rows:
        print(f"⚠️ [{source}] 보관할 원본 없음")
        return None

    filtered = _filter_rows(source, rows, province)
    if not filtered:
        print(f"⚠️ [{source}] {province} 원본 없음 — 보관 스킵")
        return None

    # ✅ [PIPA] 비식별화 (필터링 후, 저장 직전)
    filtered = _redact_pii(filtered)

    os.makedirs(os.path.join(RAW_DIR, source), exist_ok=True)
    tag = province or 'nationwide'
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')

    path = os.path.join(RAW_DIR, source, f'{tag}_{ts}.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(filtered, f, ensure_ascii=False, indent=2)

    latest = os.path.join(RAW_DIR, source, f'{tag}_latest.json')
    with open(latest, 'w', encoding='utf-8') as f:
        json.dump(filtered, f, ensure_ascii=False, indent=2)

    _prune(os.path.join(RAW_DIR, source))
    print(f"💾 원본 보관: {path} ({len(filtered)}건, PII 비식별화 완료)")
    return path


# ✅ [일회성] 기존 raw 파일의 PII 제거
#def cleanup_existing_pii():
#    count = 0
#    for f in glob.glob(os.path.join(RAW_DIR, '*', '*.json')):
#        try:
#            with open(f, encoding='utf-8') as fh:
#               rows = json.load(fh)
#            if isinstance(rows, list):
#                _redact_pii(rows)
#                with open(f, 'w', encoding='utf-8') as fh:
#                    json.dump(rows, fh, ensure_ascii=False)
#                count += 1
#        except Exception:
#            pass
#    print(f"🧹 기존 raw 파일 {count}개 PII 정리 완료")


#if __name__ == '__main__':
    cleanup_existing_pii()