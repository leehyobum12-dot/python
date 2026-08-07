# collect_certifications.py (v1.1 - 정정 버전)
import pandas as pd
import requests
import os
import io
import time
from dotenv import load_dotenv
from typing import List, Dict

load_dotenv()
DATA_GO_KR_KEY = os.getenv("DATA_GO_KR_KEY")

# 표준 레코드
class CertRecord:
    def __init__(self, source, cert_type, raw_name, raw_address, lat, lng):
        self.source = source
        self.cert_type = cert_type
        self.raw_name = raw_name
        self.raw_address = raw_address
        self.lat = lat
        self.lng = lng

# ─────────────────────────────────────────────
# 어댑터 1: REST/OpenAPI JSON 통합 (3개 소스 공통)
# ─────────────────────────────────────────────
def collect_api_json(
    service_id: str,
    cert_type: str,
    field_mapping: Dict[str, str],
    items_path: List[str] = ['response', 'body', 'items', 'item']
) -> List[CertRecord]:
    """
    공공데이터포털 REST API / 오픈API 공통 처리 (JSON 응답)
    - field_mapping: 소스별 필드명 매핑
    - items_path: JSON 응답에서 items 배열까지의 경로
    """
    records = []
    page = 1
    per_page = 500
    
    while True:
        url = f"https://apis.data.go.kr/{service_id}"
        params = {
            "serviceKey": DATA_GO_KR_KEY,
            "pageNo": page,
            "numOfRows": per_page,
            "type": "json"
        }
        
        try:
            res = requests.get(url, params=params, timeout=30)
            if res.status_code != 200:
                print(f"⚠️ [{cert_type}] HTTP {res.status_code}")
                break
            
            data = res.json()
            
            # items_path 따라가며 items 배열 추출
            items = data
            for key in items_path:
                items = items.get(key, []) if isinstance(items, dict) else []
            
            if not items:
                break
            
            for item in items:
                lat_val = item.get(field_mapping.get('lat')) if field_mapping.get('lat') else None
                lng_val = item.get(field_mapping.get('lng')) if field_mapping.get('lng') else None
                
                records.append(CertRecord(
                    source=service_id,
                    cert_type=cert_type,
                    raw_name=str(item.get(field_mapping['name'], '')),
                    raw_address=str(item.get(field_mapping['address'], '')),
                    lat=float(lat_val) if lat_val else None,
                    lng=float(lng_val) if lng_val else None,
                ))
            
            if len(items) < per_page:
                break
            page += 1
            time.sleep(0.5)  # Rate limiting
            
        except Exception as e:
            print(f"⚠️ [{cert_type}] 페이지 {page} 오류: {e}")
            break
    
    print(f"✅ [{cert_type}] {len(records)}건 수집 (API JSON)")
    return records

# ─────────────────────────────────────────────
# 어댑터 2: 표준데이터셋 CSV 파일 (모범음식점 전용)
# ─────────────────────────────────────────────
def collect_standard_csv(
    service_id: str,
    cert_type: str,
    field_mapping: Dict[str, str]
) -> List[CertRecord]:
    """
    표준데이터셋 CSV 파일 다운로드 (모범음식점만 해당)
    - 페이지네이션 없이 전체 파일 1회 다운로드
    """
    records = []
    url = f"https://apis.data.go.kr/{service_id}"
    params = {"serviceKey": DATA_GO_KR_KEY, "type": "csv"}
    
    try:
        res = requests.get(url, params=params, timeout=120)  # 파일 크므로 타임아웃 길게
        if res.status_code != 200:
            raise Exception(f"HTTP {res.status_code}")
        
        df = pd.read_csv(io.StringIO(res.text))
        
        for _, row in df.iterrows():
            lat_val = row.get(field_mapping.get('lat')) if field_mapping.get('lat') else None
            lng_val = row.get(field_mapping.get('lng')) if field_mapping.get('lng') else None
            
            records.append(CertRecord(
                source=service_id,
                cert_type=cert_type,
                raw_name=str(row.get(field_mapping['name'], '')) if pd.notna(row.get(field_mapping['name'])) else '',
                raw_address=str(row.get(field_mapping['address'], '')) if pd.notna(row.get(field_mapping['address'])) else '',
                lat=float(lat_val) if pd.notna(lat_val) else None,
                lng=float(lng_val) if pd.notna(lng_val) else None,
            ))
        
        print(f"✅ [{cert_type}] {len(records)}건 수집 (표준 CSV 파일)")
        
    except Exception as e:
        print(f"⚠️ [{cert_type}] 오류: {e}")
    
    return records

# ─────────────────────────────────────────────
# 메인: 4개 소스 수집 (정확한 어댑터 매핑)
# ─────────────────────────────────────────────
def collect_all() -> List[CertRecord]:
    all_records = []
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 1. 나트륨 줄이기 실천음식점 (REST API / JSON)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    all_records.extend(collect_api_json(
        service_id="B553572/NaClReductionRestaurantList",  # 포털에서 service_id 확인
        cert_type="low_sodium",
        field_mapping={
            'name': 'bsshNm',           # 업소명
            'address': 'rdnmAdr',       # 도로명주소
            'lat': 'lat',
            'lng': 'lon'
        }
    ))
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 2. 전국모범음식점표준데이터 (표준 CSV 파일 다운로드)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    all_records.extend(collect_standard_csv(
        service_id="1162791/ModelRestaurantStandardData",
        cert_type="model_restaurant",
        field_mapping={
            'name': '업소명',
            'address': '도로명주소',
            'lat': '위도',
            'lng': '경도'
        }
    ))
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 3. 식품안심업소 위생등급 (오픈API / JSON)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    all_records.extend(collect_api_json(
        service_id="1552134/FoodSafetyGradeRestaurant",
        cert_type="food_safety",
        field_mapping={
            'name': 'bsshNm',
            'address': 'rdnmAdr',
            'lat': 'yPos',
            'lng': 'xPos'
        }
    ))
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 4. 안심식당 (오픈API / JSON)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    all_records.extend(collect_api_json(
        service_id="1162791/SafeRestaurant",
        cert_type="safe_restaurant",
        field_mapping={
            'name': 'restntNm',
            'address': 'rdnmAdr',
            'lat': 'lat',
            'lng': 'lot'
        }
    ))
    
    return all_records

# ─────────────────────────────────────────────
# CSV 내보내기
# ─────────────────────────────────────────────
def export_to_csv(records: List[CertRecord], output_path: str):
    df = pd.DataFrame([{
        'source': r.source,
        'cert_type': r.cert_type,
        'store_name': r.raw_name,
        'address': r.raw_address,
        'lat': r.lat,
        'lng': r.lng,
    } for r in records])
    df.to_csv(output_path, index=False, encoding='utf-8-sig')
    print(f"📄 CSV 저장: {output_path} ({len(df)}건)")

if __name__ == "__main__":
    print("🚀 전국구 배지 데이터 수집 시작 (v1.1 정정 버전)...")
    records = collect_all()
    export_to_csv(records, "data/certifications_raw.csv")
    print(f"\n🎉 완료: 총 {len(records)}건 수집")
    
    # 소스별 통계
    from collections import Counter
    stats = Counter(r.cert_type for r in records)
    print("\n📊 소스별 통계:")
    for cert, count in stats.items():
        print(f"   {cert}: {count}건")