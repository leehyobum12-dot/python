"""
avoid_tags 생성 모듈 (알레르기 기반 + 데이터 품질 플래그)
===========================================================
- avoid_tags: 알레르기 회피 태그 (메뉴/업태 기반)
- store_name_quality: 데이터 품질 플래그 (별도 관리)

이력:
- v1: 알레르기 기반 (원래 방식)
- v2: 업태 기반으로 변경 시도 → 사용자 피드백으로 원상복구
- v3: 알레르기 기반 복원 + 2글자 가게 이름 예외 처리 + 네이버/카카오 검증 플래그

avoid_tags 예시:
    ['육류', '갑각류', '조개류']

store_name_quality 예시:
    'valid'          - 검증 완료 (또는 검증 불필요)
    'needs_verify'   - 네이버/카카오 검증 필요
"""

from __future__ import annotations

import re

# ═══════════════════════════════════════════════════════════
# 알레르기 매핑 (메뉴 기반)
# ═══════════════════════════════════════════════════════════

ALLERGEN_MAPPING = {
    '갑각류·조개류': [
        '갑각류',
        '조개류',
        '새우',
        '게',
        '랍스터',
        '오징어',
        '문어',
        '굴',
        '조개',
        '홍합',
        '전복',
        '가리비',
    ],
    '생선·연체류': [
        '생선',
        '연체류',
        '고등어',
        '갈치',
        '꽁치',
        '삼치',
        '조기',
        '장어',
        '민어',
        '참치',
        '연어',
        '멸치',
    ],
    '육류': ['돼지', '소', '닭', '오리', '양', '염소', '말고기'],
    '곡류·두류': ['밀', '보리', '쌀', '콩', '두부', '메밀', '귀리', '호밀'],
    '견과류': ['땅콩', '호두', '아몬드', '캐슈넛', '피스타치오', '헤이즐넛'],
    '유제품': ['우유', '치즈', '버터', '요거트', '크림', '유청'],
    '알류': ['계란', '달걀', '메추라기알'],
    '과일류': ['복숭아', '토마토', '키위', '딸기', '사과', '포도'],
    '채소류': ['셀러리', '오이', '당근', '무'],
}

# 글루텐 매핑
GLUTEN_KEYWORDS = [
    '밀',
    '보리',
    '호밀',
    '귀리',
    '빵',
    '면',
    '국수',
    '파스타',
    '떡볶이',
    '칼국수',
    '우동',
    '소바',
    '라면',
    '짜장면',
    '짬뽕',
    '피자',
    '햄버거',
    '샌드위치',
    '케이크',
    '쿠키',
    '빵류',
]


# ═══════════════════════════════════════════════════════════
# 일반 명사 패턴 (가게 이름으로 부적합)
# ═══════════════════════════════════════════════════════════

GENERIC_NAME_PATTERNS = [
    r'^식당$',
    r'^카페$',
    r'^레스토랑$',
    r'^음식점$',
    r'^분식$',
    r'^치킨$',
    r'^피자$',
    r'^중국집$',
    r'^일식$',
    r'^한식$',
    r'^밥집$',
    r'^국밥$',
    r'^찜닭$',
    r'^횟집$',
    r'^카페$',
    r'^빵집$',
]

# 특수문자/깨진 데이터 패턴
SUSPICIOUS_PATTERNS = [
    r'[?？]',  # 물음표 포함
    r'^\d+$',  # 숫자만
    r'^[ㄱ-ㅎㅏ-ㅣ]+$',  # 자음/모음만
    r'^[A-Za-z\s]+$',  # 영문만 (한글 없음)
]


def generate_avoid_tags(
    allergen_tags: list[str] | None,
    biz_type: str | None = None,
    main_menu: str | None = None,
    menus: list[str] | None = None,
) -> list[str]:
    """
    알레르기 기반 avoid_tags 생성

    Args:
        allergen_tags: allergen_tags 목록 (메뉴에서 추출)
        biz_type: 업종/업태
        main_menu: 메인 메뉴
        menus: 메뉴 목록

    Returns:
        알레르기 회피 태그 목록
    """
    avoid_tags: list[str] = []

    # 1. allergen_tags에서 직접 추출
    if allergen_tags:
        for allergen in allergen_tags:
            if allergen in ALLERGEN_MAPPING:
                avoid_tags.extend(ALLERGEN_MAPPING[allergen])
            elif '·' in allergen:
                # '갑각류·조개류' → ['갑각류', '조개류']
                avoid_tags.extend(allergen.split('·'))

    # 2. 메뉴에서 글루텐 키워드 확인
    all_menu_texts = []
    if main_menu:
        all_menu_texts.append(main_menu)
    if menus:
        all_menu_texts.extend(menus)

    menu_text = ' '.join(all_menu_texts)
    if any(keyword in menu_text for keyword in GLUTEN_KEYWORDS):
        avoid_tags.append('글루텐')

    # 3. 중복 제거 및 정렬
    avoid_tags = sorted(set(avoid_tags))

    return avoid_tags


def assess_store_name_quality(
    store_name: str | None,
    province: str | None = None,
    municipality: str | None = None,
) -> str:
    """
    가게 이름 품질 평가

    Args:
        store_name: 가게 이름
        province: 시도
        municipality: 시군구

    Returns:
        품질 상태:
        - 'valid': 검증 완료 (또는 검증 불필요)
        - 'needs_verify': 네이버/카카오 검증 필요
    """
    if not store_name:
        return 'needs_verify'

    name = store_name.strip()

    # 1. 너무 짧음 (1글자)
    if len(name) <= 1:
        return 'needs_verify'

    # 2. 일반 명사만 있는 경우
    for pattern in GENERIC_NAME_PATTERNS:
        if re.match(pattern, name):
            return 'needs_verify'

    # 3. 특수문자/깨진 데이터
    for pattern in SUSPICIOUS_PATTERNS:
        if re.search(pattern, name):
            return 'needs_verify'

    # 4. 특수문자 과다 (한글/영문/숫자 비율이 50% 미만)
    valid_chars = len(re.findall(r'[\uAC00-\uD7A3a-zA-Z0-9]', name))
    if valid_chars / len(name) < 0.5:
        return 'needs_verify'

    # 5. 주소 정보 불완전
    if not province or not municipality:
        return 'needs_verify'

    return 'valid'


def needs_store_verification(store_name_quality: str) -> bool:
    """
    네이버/카카오 검증 필요 여부

    Args:
        store_name_quality: assess_store_name_quality() 결과

    Returns:
        True: 검증 필요
    """
    return store_name_quality == 'needs_verify'


def build_search_query(
    province: str | None,
    municipality: str | None,
    emd: str | None,
    address_road: str | None,
    biz_type: str | None,
    store_name: str | None,
) -> str:
    """
    네이버/카카오 지도 검색 쿼리 생성

    Args:
        province: 시도
        municipality: 시군구
        emd: 읍면동
        address_road: 도로명 주소
        biz_type: 업종/업태
        store_name: 가게 이름 (참고용)

    Returns:
        검색 쿼리 문자열
    """
    parts = []

    # 주소 기반 검색 (우선)
    if address_road:
        parts.append(address_road)
    else:
        if province:
            parts.append(province)
        if municipality:
            parts.append(municipality)
        if emd:
            parts.append(emd)

    # 업종 추가
    if biz_type:
        parts.append(biz_type)

    # 가게 이름이 유효하면 추가
    if store_name and len(store_name.strip()) > 2:
        if not any(re.match(p, store_name.strip()) for p in GENERIC_NAME_PATTERNS):
            parts.append(store_name)

    return ' '.join(parts)
