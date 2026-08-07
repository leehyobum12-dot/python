# # enrich/naver_places.py (신규)- 정밀하게 작업해야 할 때 or 틀린 정보만 수정할 때 - 건당 유료
# import requests
# import os

# def search_place_by_address(address: str, client_id: str, client_secret: str) -> str:
#     """주소로 네이버 Places 검색 → 첫 번째 결과의 상호명 반환"""
#     url = "https://openapi.naver.com/v1/search/local.json"
#     headers = {
#         "X-Naver-Client-Id": client_id,
#         "X-Naver-Client-Secret": client_secret,
#     }
#     params = {
#         "query": address,
#         "display": 1,
#         "start": 1,
#         "sort": "random",
#     }
    
#     res = requests.get(url, headers=headers, params=params, timeout=10)
#     if res.status_code != 200:
#         return None
    
#     data = res.json()
#     items = data.get("items", [])
#     if not items:
#         return None
    
#     # HTML 태그 제거
#     import re
#     title = re.sub(r'<[^>]+>', '', items[0].get('title', ''))
#     return title.strip()


# def fix_damaged_names(records: list) -> list:
#     """손상된 상호명을 네이버 Places API로 복구"""
#     client_id = os.getenv("NAVER_CLIENT_ID")
#     client_secret = os.getenv("NAVER_CLIENT_SECRET")
    
#     if not client_id or not client_secret:
#         print("⚠️ NAVER API 키 미설정 — 상호명 복구 스킵")
#         return records
    
#     fixed = 0
#     for r in records:
#         if '？' in (r.raw_name or '') or '?' in (r.raw_name or ''):
#             recovered = search_place_by_address(r.raw_address, client_id, client_secret)
#             if recovered and len(recovered) >= 2:
#                 print(f"✅ 복구: '{r.raw_name}' → '{recovered}' (주소: {r.raw_address})")
#                 r.raw_name = recovered
#                 fixed += 1
    
#     print(f"🔧 상호명 복구 완료: {fixed}건")
#     return records