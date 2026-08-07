import pandas as pd
import requests
import os
import re
from dotenv import load_dotenv
import time

# ==========================================
# 1. 환경 변수 설정 (.env 파일 로드)
# ==========================================
load_dotenv()
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")

# ==========================================
# 2. 통합 데이터 사전 (대표 메뉴)
# ==========================================
SEARCHED_MENUS = {
    1: "갈비찜, 갈비탕, 낙지가리탕, 전복가리탕", 2: "소머리해장국, 갈비탕, 내장탕", 
    3: "양념갈비, 오겹살, 차돌박이, 고사리육개장", 4: "고사리육개장, 소고기해장국, 감자탕",
    5: "추어탕", 6: "왕갈비탕", 7: "야채비빔밥, 순두부", 8: "해장국, 소고기국밥, 내장탕, 닭곰탕",
    9: "멍게비빔밥, 고등어구이, 꼬막비빔밥, 성게비빔밥", 10: "순대국밥, 막창, 찹쌀모듬순대",
    11: "설렁탕, 해장국, 갈비탕, 도가니탕, 소머리국밥", 12: "활어회, 대방어회, 돔지리, 낙지볶음", 
    14: "소고기해장국", 15: "꼬리곰탕전골, 설렁탕, 해장국", 16: "돼지등갈비찜",
    17: "설렁탕, 갈비탕, 매생이갈비탕, 도가니탕", 18: "소고기선지해장국, 한우내장탕, 한우소머리곰탕",
    19: "추어탕, 통추어탕, 추어튀김", 21: "소고기선지해장국, 양곰탕, 도가니탕", 22: "소머리곰탕",
    62: "스텔라떡볶이, 생크림빵떡볶이, 모둠튀김", 63: "핫델리 치킨, 델리 튀김류, 분식",
    64: "델리 튀김류, 분식, 즉석조리식품", 65: "뿌링클, 후라이드치킨, 맛초킹",
    66: "뿌링클, 골드킹, 후라이드치킨", 67: "뿌링클, 맛초킹, 후라이드치킨",
    68: "황금올리브치킨, 자메이카 통다리구이", 69: "한스패밀리 뷔페, 한식 뷔페",
    70: "이탈리안 비엠티, 에그마요, 써브웨이 클럽", 71: "신화월드 성화정 한식, 차이니스 다이닝",
    72: "이탈리안 비엠티, 에그마요, 쉬림프", 73: "맵슐랭치킨, 후라이드치킨, 생맥주",
    74: "터틀리애 수제 피자, 파스타", 75: "롯데스카이힐CC 클럽하우스 단품 및 코스요리",
    76: "롯데스카이힐CC 스타트하우스 간식 및 단품", 77: "제주 흑돼지 삼겹살, 흑돼지 목살",
    78: "전복죽, 소고기야채죽, 비빔밥", 79: "파르나스호텔 콘페티 뷔페, 조식 뷔페",
    80: "교촌허니콤보, 교촌시그니처, 교촌레드", 81: "교촌허니콤보, 교촌오리지날, 교촌레드",
    82: "해비치 메르&테르 프렌치 다이닝 코스", 83: "해비치 이디 다이닝, 캐주얼 양식",
    84: "해비치 하노루 흑돼지 구이, 한식 정식", 85: "해비치 나인티나인 바 & 레스토랑",
    86: "해비치 밀리우 파인다이닝 프렌치 코스", 87: "해비치 섬모라 라이브 뷔페",
    88: "스텔라떡볶이, 생크림빵떡볶이, 튀김", 89: "더플레이스 이탈리안 파스타, 화덕피자",
    90: "60계 간지치킨, 고추치킨, 크크크치킨", 91: "맥도날드 빅맥, 1955 버거, 맥스파이시 상하이버거",
    92: "60계 간지치킨, 고추치킨, 크크크치킨", 93: "황금올리브치킨, 자메이카 통다리구이",
    94: "아란 한식 정식, 덮밥, 식사류", 95: "빽보이 울트라 빽보이 피자, 체다 고구마 피자",
    96: "항해진미 일식 회, 카이센동, 초밥", 97: "제주선 더블랙 흑돼지 구이 코스",
    98: "윙클스 피자리아 화덕피자, 파스타", 99: "윙클스 비스트로 양식 코스, 스테이크",
    100: "신화월드 스카이 온 파이브 다이닝 프리미엄 뷔페", 101: "신화월드 제주선 제주 향토 한식 코스",
    102: "소노캄 제주 BBQ 황금올리브치킨", 103: "도미노 포테이토 피자, 블랙타이거 쉬림프 피자",
    104: "네네치킨 스노윙치킨, 파닭, 반반치킨", 105: "켄싱턴리조트 돌미롱 제주 향토 한식 정식",
    106: "켄싱턴리조트 애슐리 퀸즈 뷔페", 107: "켄싱턴리조트 애슐리 퀸즈 뷔페",
    108: "60계 간지치킨, 고추치킨, 크크크치킨", 109: "그랜드조선 제주 그랑제이 라운지 & 다이닝",
    110: "그랜드조선 제주 루브리카 이탈리안 스테이크 & 파스타", 111: "그랜드조선 제주 아리아 뷔페",
    112: "BBQ 황금올리브치킨, 자메이카 통다리구이", 113: "디리쉬어스 캐주얼 다이닝, 수제버거",
    114: "신화테라스 캐주얼 다이닝, 뷔페", 115: "신화월드 랜딩 다이닝 해산물 뷔페",
    116: "교촌허니콤보, 교촌오리지날, 교촌레드", 117: "포도호텔 다이닝룸 수제 우동, 옥돔구이, 한식 정식",
    118: "BBQ 황금올리브치킨, 자메이카 통다리구이", 119: "두찜 까만찜닭, 로제찜닭, 트러플크림찜닭",
    120: "BHC 뿌링클, 맛초킹, 후라이드치킨", 121: "푸라닭 블랙알리오, 고추마요치킨",
    122: "60계 간지치킨, 고추치킨, 크크크치킨", 123: "맥도날드 빅맥, 1955 버거, 맥스파이시 상하이버거",
    124: "푸라닭 블랙알리오, 고추마요치킨", 125: "명륜진사갈비 숯불돼지갈비 무한리필",
    126: "중문우동한그릇 수제 우동, 모둠 튀김", 127: "진미명가식당 다라치, 꿩요리, 한식 정식",
    128: "60계 간지치킨, 고추치킨, 크크크치킨", 129: "교촌허니콤보, 교촌오리지날, 교촌레드",
    130: "BBQ 황금올리브치킨, 자메이카 통다리구이"
}

# ==========================================
# 3. 핵심 유틸리티 함수들
# ==========================================

def get_naver_location_data(address):
    """
    네이버 Geocoding API를 호출하여 정제된 주소와 좌표를 한 번에 반환합니다.
    """
    if pd.isna(address) or str(address).strip() == '':
        return None
    
    url = f"https://maps.apigw.ntruss.com/map-geocode/v2/geocode?query={address}"
    headers = {
        "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID.strip(),
        "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET.strip(),
        "Referer": "http://localhost"
    }
    
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            if data.get('addresses'):
                addr_obj = data['addresses'][0]
                
                # 시, 읍/면/동 추출
                sido, sigugun, dongmyun = "", "", ""
                for elem in addr_obj.get('addressElements', []):
                    types = elem.get('types', [])
                    if "SIDO" in types: sido = elem.get('longName', '')
                    elif "SIGUGUN" in types: sigugun = elem.get('longName', '')
                    elif "DONGMYUN" in types: dongmyun = elem.get('longName', '')
                
                city = sigugun if sigugun else sido
                
                return {
                    '시': city,
                    '읍면동': dongmyun,
                    'region (지역)': f"{city} {dongmyun}".strip() if dongmyun else city,
                    '도로명주소': addr_obj.get('roadAddress', address),
                    '지번주소': addr_obj.get('jibunAddress', ''),
                    'lat': float(addr_obj.get('y', 0)),
                    'lng': float(addr_obj.get('x', 0))
                }
    except Exception as e:
        print(f"⚠️ API 호출 오류 ({address}): {e}")
    return None

def fill_menu(row):
    """결측된 메뉴 데이터를 사전 또는 기존 컬럼 값으로 채웁니다."""
    if pd.notna(row.get('대표메뉴')) and str(row.get('대표메뉴')).strip() not in ['', 'nan', 'NaN']:
        return str(row['대표메뉴']).strip()
    
    row_id = row.get('id')
    if row_id in SEARCHED_MENUS:
        return SEARCHED_MENUS[row_id]
        
    if pd.notna(row.get('menu_name')) and str(row.get('menu_name')).strip() not in ['-', '']:
        return str(row['menu_name']).strip()
    
    return ""

def infer_tags(menu_string):
    """메뉴명 기반으로 질환/건강 주의 태그를 자동 생성합니다."""
    menu = str(menu_string)
    if any(k in menu for k in ['치킨', '후라이드', '튀김', '돈까스', '돈가스']):
        return "⚠️대사성 질환 주의 (고지방/튀김류)"
    if any(k in menu for k in ['아구찜', '해물찜', '두루치기', '낙지볶음', '양념갈비', '갈비찜', '갈비', '찜닭', '숯불']):
        return "⚠️순환기·대사성 주의 (자극적인 양념)"
    if any(k in menu for k in ['족발', '보쌈', '곱창', '막창', '대창']):
        return "⚠️순환기·대사성 주의 (자극적인 양념/고지방)"
    if any(k in menu for k in ['해장국', '국밥', '찌개', '탕', '설렁탕', '곰탕', '육개장', '우동']):
        return "⚠️순환기계 질환 주의 (고지방/고나트륨 국물)"
    return "✅일반식 (적당량 섭취 시 무해)"

def search_naver_place(store_name, region):
    """(유틸리티) 네이버 Local API를 활용한 장소 추가 검색 함수"""
    url = "https://openapi.naver.com/v1/search/local.json"
    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID.strip(),
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET.strip()
    }
    params = {"query": f"{region} {store_name}", "display": 5, "sort": "comment"}
    return requests.get(url, headers=headers, params=params).json()

# ==========================================
# 4. 메인 파이프라인 실행
# ==========================================
def main():
    INPUT_FILE = '제주도_인증식당_원본.xlsx' # 원본 파일명 입력
    OUTPUT_FILE = '제주도_인증식당_최종정제완료.xlsx'
    
    print(f"🚀 데이터 파이프라인 시작: {INPUT_FILE} 로드 중...")
    df = pd.read_excel(INPUT_FILE)
    
    # [Step 1] 컬럼명 자동 보정
    rename_map = {
        'id (번호)': 'id', 'menu_name (대표나트륨 저감 메뉴)': 'menu_name',
        'lat (위도)': 'lat', 'Ing': 'lng', 'lng (경도)': 'lng_old', 'avoid_tags (피해야할 태그)': 'avoid_tags'
    }
    df.rename(columns=rename_map, inplace=True)
    
    # [Step 2] 메뉴 및 태그 채우기
    print("📝 대표 메뉴 및 질환별 필터링 태그 자동 생성 중...")
    df['대표메뉴'] = df.apply(fill_menu, axis=1)
    df['avoid_tags'] = df['대표메뉴'].apply(infer_tags)
    
    # [Step 3] 네이버 API를 통한 주소 및 좌표 표준화
    total_count = len(df)
    print(f"📍 네이버 API 기반 주소/좌표 통합 정제 시작 (총 {total_count}건)...")
    
    for idx in df.index:
        raw_addr = df.loc[idx, '도로명주소'] if '도로명주소' in df.columns else df.loc[idx, '도로명주소 (경도)']
        details = get_naver_location_data(raw_addr)
        
        if details:
            df.loc[idx, '시'] = details['시']
            df.loc[idx, '읍면동'] = details['읍면동']
            df.loc[idx, 'region (지역)'] = details['region (지역)']
            df.loc[idx, '도로명주소'] = details['도로명주소']
            df.loc[idx, '지번주소'] = details['지번주소']
            df.loc[idx, 'lat'] = details['lat']
            df.loc[idx, 'lng'] = details['lng']
            
        if (idx + 1) % 50 == 0 or (idx + 1) == total_count:
            print(f"   진행률: {idx + 1}/{total_count}건 완료")
            time.sleep(0.5) # API 호출 제한 방지 쿨다운
            
    # [Step 4] 컬럼명 최종 정리 및 저장
    cols_to_drop = [c for c in ['Ing', 'lng_old', 'menu_name'] if c in df.columns]
    df.drop(columns=cols_to_drop, inplace=True, errors='ignore')
    
    df.rename(columns={
        'id': 'id (번호)',
        'lat': 'lat (위도)',
        'lng': 'lng (경도)',
        'avoid_tags': 'avoid_tags (피해야할 태그)'
    }, inplace=True)
    
    column_order = [
        'id (번호)', 'name (식당명)', '시', '읍면동', 'region (지역)', 
        '도로명주소', '지번주소', '대표메뉴', '업태', 'lat (위도)', 'lng (경도)', 
        'certs (인증마크)', 'avoid_tags (피해야할 태그)'
    ]
    existing_cols = [c for c in column_order if c in df.columns]
    
    # 없는 컬럼은 제외하고 재배열
    final_df = df[existing_cols + [c for c in df.columns if c not in existing_cols]]
    
    final_df.to_excel(OUTPUT_FILE, index=False)
    print(f"\n🎉 파이프라인 처리 완료! 결과물이 '{OUTPUT_FILE}'로 저장되었습니다.")

if __name__ == "__main__":
    main()