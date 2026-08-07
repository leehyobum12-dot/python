# data_pipeline/storage/checkpoint.py
import os
import json
import gzip
from datetime import datetime
from typing import List, Dict
from adapters.base import CertRecord

CHECKPOINT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'checkpoint')


def save_checkpoint(source: str, province: str, records: List[CertRecord]):
    """수집 중간 결과를 디스크에 저장 (크래시 시 복구용)"""
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"{source}_{province or '전국'}_{timestamp}.json.gz"
    filepath = os.path.join(CHECKPOINT_DIR, filename)
    
    # CertRecord → dict 변환
    data = []
    for r in records:
        data.append({
            'source': r.source, 'cert_type': r.cert_type,
            'raw_name': r.raw_name, 'raw_address': r.raw_address,
            'province': r.province, 'municipality': r.municipality, 'emd': r.emd,
            'road_name': r.road_name, 'lot_num': r.lot_num,
            'phone': r.phone, 'biz_type': r.biz_type, 'main_menu': r.main_menu,
            'designated_at': r.designated_at,
            'cancelled_at': r.cancelled_at, 'closed_at': r.closed_at,
            'is_active': r.is_active, 'lat': r.lat, 'lng': r.lng,
        })
    
    # gzip 압축으로 저장 (용량 1/10)
    with gzip.open(filepath, 'wt', encoding='utf-8') as f:
        json.dump({'count': len(data), 'records': data}, f, ensure_ascii=False)
    print(f"💾 체크포인트 저장: {filepath} ({len(data)}건)")


def load_latest_checkpoint(source: str, province: str) -> List[Dict]:
    """가장 최근 체크포인트 로드"""
    if not os.path.exists(CHECKPOINT_DIR):
        return []
    pattern = f"{source}_{province or '전국'}_"
    files = [f for f in os.listdir(CHECKPOINT_DIR) if f.startswith(pattern)]
    if not files:
        return []
    latest = sorted(files)[-1]
    filepath = os.path.join(CHECKPOINT_DIR, latest)
    print(f"💾 체크포인트 로드: {latest}")
    with gzip.open(filepath, 'rt', encoding='utf-8') as f:
        return json.load(f)['records']