# enrich_coordinates.py
#
# 좌표가 없는 행만 네이버 Geocoding API로 보강
# (이전 대화에서 제공한 코드 재활용)

import pandas as pd
import requests
import os
import time
from dotenv import load_dotenv

load_dotenv()
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")

def geocode_naver(address: str) -> Optional[Dict]:
    """네이버 Maps Geocoding: 주소 → province/municipality/emd/lat/lng"""
    if pd.isna(address) or not str(address).strip():
        return None
    
    url = f"https://maps.apigw.ntruss.com/map-geocode/v2/geocode?query={address}"
    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
        "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET,
        "Referer": "http://localhost"
    }
    
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code != 200:
            return None
        
        data = res.json()
        if not data.get('addresses'):
            return None
        
        addr = data['addresses'][0]
        province = municipality = emd = ""
        for elem in addr.get('addressElements', []):
            types = elem.get('types', [])
            if "SIDO" in types: province = elem.get('longName', '')
            elif "SIGUGUN" in types: municipality = elem.get('longName', '')
            elif "DONGMYUN" in types: emd = elem.get('longName', '')
        
        return {
            'province': province,
            'municipality': municipality,
            'emd': emd,
            'road_name': addr.get('roadAddress', address),
            'lot_num': addr.get('jibunAddress', ''),
            'lat': float(addr.get('y', 0)),
            'lng': float(addr.get('x', 0)),
        }
    except Exception as e:
        print(f"⚠️ Geocoding 오류 ({address}): {e}")
        return None

def enrich_missing_coordinates(input_csv: str, output_csv: str):
    """좌표가 없는 행만 Geocoding으로 보강"""
    df = pd.read_csv(input_csv)
    
    total = len(df)
    missing = df[df['lat'].isna() | df['lng'].isna()]
    print(f"📍 좌표 보강 대상: {len(missing)}/{total}건")
    
    enriched = 0
    for idx, row in missing.iterrows():
        result = geocode_naver(row['address'])
        if result:
            for k, v in result.items():
                df.loc[idx, k] = v
            enriched += 1
        
        if (enriched + 1) % 50 == 0:
            print(f"   진행: {enriched}/{len(missing)}")
        
        time.sleep(0.2)  # Rate limiting
    
    df.to_csv(output_csv, index=False)
    print(f"✅ 보강 완료: {enriched}건 ({output_csv})")

if __name__ == "__main__":
    enrich_missing_coordinates(
        "data/certifications_raw.csv",
        "data/certifications_enriched.csv"
    )