# data_pipeline/storage/raw_archiver.py
import json, os
from datetime import datetime

RAW_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'raw')


def archive_raw(source: str, rows: list, province: str = None):
    if not rows:
        print(f"⚠️ [{source}] 보관할 원본 없음")
        return None
    os.makedirs(os.path.join(RAW_DIR, source), exist_ok=True)
    tag = province or 'nationwide'
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')

    path = os.path.join(RAW_DIR, source, f'{tag}_{ts}.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)

    latest = os.path.join(RAW_DIR, source, f'{tag}_latest.json')
    with open(latest, 'w', encoding='utf-8') as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)

    print(f"💾 원본 보관: {path} ({len(rows)}건)")
    return path