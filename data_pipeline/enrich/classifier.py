# data_pipeline/enrich/classifier.py
"""메뉴명 기반 7차원 태깅 엔진 (v1 휴리스틱)"""

from typing import Optional, Set, Tuple, List, Dict

# ─────────────── 재료 키워드 ───────────────
INGREDIENT_KEYWORDS = {
    '돼지': ['돼지', '흑돼지', '돈가스', '돈까스', '삼겹', '목살', '항정', '족발', '보쌈', '제육'],
    '소': ['소고기', '쇠고기', '한우', '등심', '안심', '차돌', '도가니', '설렁', '곰탕', '육개장', '갈비', '스테이크'],
    '닭': ['닭', '치킨', '통닭', '파닭', '닭갈비', '삼계', '백숙'],
    '양': ['양고기', '양꼬치'],
    '수산물': ['회', '생선', '고등어', '갈치', '장어', '전복', '새우', '조개', '굴', '물회', '해물', '해산',
               '오징어', '주꾸미', '낙지', '멍게', '성게', '옥돔', '삼치', '방어', '돔', '아귀', '소라'],
}

# ─────────────── 지역 특산 키워드 ───────────────
REGIONAL_KEYWORDS = {
    ('제주', '흑돼지'): ['흑돼지', '흑돈', '돗베'], ('제주', '갈치'): ['갈치'],
    ('제주', '전복'): ['전복'], ('제주', '소라'): ['소라', '뿔소라'], ('제주', '성게'): ['성게'],
    ('제주', '한치'): ['한치'], ('제주', '옥돔'): ['옥돔'], ('제주', '메밀'): ['메밀'], ('제주', '몸국'): ['몸국'],
    ('부산·경남', '돼지국밥'): ['돼지국밥'], ('부산·경남', '밀면'): ['밀면'], ('부산·경남', '꼼장어'): ['꼼장어'],
    ('전라', '비빔밥'): ['비빔밥'], ('전라', '콩나물국밥'): ['콩나물국밥'],
    ('강원', '닭갈비'): ['닭갈비'], ('강원', '막국수'): ['막국수'],
    ('서울·경기', '설렁탕'): ['설렁탕'], ('이북식', '냉면'): ['냉면'], ('이북식', '만두'): ['만두'],
}

# ─────────────── 알레르기 키워드 ───────────────
ALLERGEN_KEYWORDS = {
    ('갑각류·조개류', '새우'): ['새우', '대하'], ('갑각류·조개류', '게'): ['게', '게장'],
    ('갑각류·조개류', '조개'): ['조개'], ('갑각류·조개류', '굴'): ['굴', '굴국밥'],
    ('갑각류·조개류', '전복'): ['전복'],
    ('생선·연체류', '고등어'): ['고등어'], ('생선·연체류', '갈치'): ['갈치'],
    ('생선·연체류', '오징어'): ['오징어'], ('생선·연체류', '낙지'): ['낙지'], ('생선·연체류', '주꾸미'): ['주꾸미'],
    ('곡류·두류', '밀'): ['면', '국수', '칼국수', '짜장', '짬', '빵', '만두', '파스타'],
    ('곡류·두류', '메밀'): ['메밀'], ('곡류·두류', '콩'): ['두부', '된장', '청국장', '콩나물'],
    ('유제품·알류', '우유'): ['치즈', '크림', '우유', '라떼'], ('유제품·알류', '계란'): ['계란', '달걀', '오믈렛'],
    ('견과·땅콩', '땅콩'): ['땅콩'], ('견과·땅콩', '호두'): ['호두'],
}

# ─────────────── 상황 키워드 ───────────────
OCCASION_KEYWORDS = {
    ('뷰', '오션뷰'): ['오션뷰', '바다뷰', '해변'], ('뷰', '한옥'): ['한옥'], ('뷰', '야경'): ['야경', '루프탑'],
    ('코스·파인', '코스'): ['코스'], ('코스·파인', '파인다이닝'): ['파인'],
    ('프라이빗', '룸'): ['룸', '독실', '프라이빗'], ('단체', '뷔페'): ['뷔페'],
    ('이벤트', '회갑'): ['회갑', '환갑'], ('이벤트', '돌잔치'): ['돌잔치'],
}


def _match(rules: Dict[Tuple[str, str], List[str]], item: str) -> Set[str]:
    """규칙 기반 태그 매칭 (대분류 + 소분류)"""
    out = set()
    for (p, s), kws in rules.items():
        if any(k in item for k in kws):
            out.add(p)
            out.add(f'{p}:{s}')
    return out


def _detect_ingredients(item: str) -> List[str]:
    """재료 감지 (돼지/소 감지 시 소의 약한 키워드 제거)"""
    found = [k for k, kws in INGREDIENT_KEYWORDS.items() if any(w in item for w in kws)]
    # ✅ 돼지 감지 시, 소의 "약한 키워드"(갈비·스테이크) 매칭 제거
    if '돼지' in found and '소' in found:
        if not any(w in item for w in ('소고기', '쇠고기', '한우')):
            found.remove('소')
    return found


def _detect_cooking(item: str, ingredients: List[str]) -> Set[str]:
    """조리법 감지"""
    out = set()
    if '탕수육' in item: out.add('튀김')
    if '국' in item and '국수' not in item: out |= {'국물', '국물:국'}
    if '찌개' in item: out |= {'국물', '국물:찌개'}
    if '전골' in item: out |= {'국물', '국물:전골'}
    if item.endswith('탕') and '탕수육' not in item: out |= {'국물', '국물:탕'}
    if any(k in item for k in ('구이', '스테이크', '바비큐', '바베큐', '훈제')):
        out.add('구이')
        for ing in ingredients: out.add(f'구이:{ing}')
    if '볶음' in item: out.add('볶음')
    if '조림' in item: out.add('조림')
    if '찜' in item: out.add('찜')
    if any(k in item for k in ('튀김', '탕수육', '까스', '커틀릿', '프라이드')): out.add('튀김')
    return out


def _detect_nutrient(item: str, ingredients: List[str], cook_set: Set[str]) -> Set[str]:
    """영양소 감지"""
    out = set()
    if '죽' in item: out |= {'탄수화물', '탄수화물:죽'}
    if '밥' in item: out |= {'탄수화물', '탄수화물:밥'}
    if any(k in item for k in ('면', '국수', '냉면', '우동', '라멘', '라면', '파스타', '스파게티', '짜장', '짬뽕')):
        out |= {'탄수화물', '탄수화물:면'}
    if '떡' in item: out |= {'탄수화물', '탄수화물:떡'}
    if ingredients and '튀김' not in cook_set:
        for ing in ingredients: out |= {'단백질', f'단백질:{ing}'}
    if '튀김' in cook_set or '삼겹' in item:
        out |= {'지방', ('지방:튀김' if '튀김' in cook_set else '지방:삼겹살')}
    if any(k in item for k in ('채소', '야채', '나물', '샐러드')): out |= {'식이섬유', '식이섬유:채소'}
    if any(k in item for k in ('미역', '다시마', '해조')): out |= {'식이섬유', '식이섬유:해조류'}
    return out


def _detect_cuisine(biz: str, item: str) -> List[Tuple[str, Optional[str]]]:
    """음식 장르 감지"""
    biz = biz or ''
    if biz.startswith('한식'): return [('한식', '향토' if '향토' in item else '일반')]
    if biz.startswith('중식') or any(k in item for k in ('짜장', '짬', '탕수육')): return [('중식', None)]
    if biz.startswith('일식') or any(k in item for k in ('초밥', '라멘', '우동')): return [('일식', None)]
    if biz.startswith('양식') or biz.startswith('서양') or any(k in item for k in ('스테이크', '파스타', '피자')):
        return [('양식', None)]
    if any(k in biz for k in ('카페', '디저트', '제과')): return [('디저트', None)]
    return [('한식', '일반')]


def _expand(pairs: List[Tuple[str, Optional[str]]]) -> Set[str]:
    """(대분류, 소분류) 페어를 태그 집합으로 확장"""
    out = set()
    for p, s in pairs:
        out.add(p)
        if s: out.add(f'{p}:{s}')
    return out


def classify(text: str, biz_type: Optional[str] = None) -> Dict[str, List[str]]:
    """
    7차원 태그 분류
    
    Args:
        text: 메뉴 텍스트 (쉼표 구분) 또는 상호명
        biz_type: 업종 분류 (Optional)
    
    Returns:
        7차원 태그 딕셔너리
    """
    # ✅ 빈 값 처리
    text = text or ''
    biz_type = biz_type or ''
    
    if not text.strip():
        # 빈 값이면 기본 태그 반환
        return {
            'cuisine_tags': ['한식', '한식:일반'],
            'nutrient_tags': [],
            'cooking_tags': [],
            'ingredient_tags': [],
            'regional_tags': [],
            'allergen_tags': [],
            'occasion_tags': [],
        }
    
    # ✅ text 파라미터 사용 (main_menu 아님!)
    items = [m.strip() for m in text.split(',') if m.strip()]
    
    cuisine, nutrient, cooking = set(), set(), set()
    ingredient, regional, allergen, occasion = set(), set(), set(), set()

    for it in items:
        ings = _detect_ingredients(it)
        cook_set = {c.split(':')[0] for c in _detect_cooking(it, ings)}
        cuisine |= _expand(_detect_cuisine(biz_type, it))
        nutrient |= _detect_nutrient(it, ings, cook_set)
        cooking |= _detect_cooking(it, ings)
        ingredient |= set(ings)
        regional |= _match(REGIONAL_KEYWORDS, it)
        allergen |= _match(ALLERGEN_KEYWORDS, it)
        occasion |= _match(OCCASION_KEYWORDS, it)
        
        # 육류 알레르기 추가
        for ing in ings:
            if ing in ('돼지', '소', '닭'):
                allergen.add('육류')
                allergen.add(f'육류:{ing}')

    return {
        'cuisine_tags': sorted(cuisine) or ['한식', '한식:일반'],
        'nutrient_tags': sorted(nutrient),
        'cooking_tags': sorted(cooking),
        'ingredient_tags': sorted(ingredient),
        'regional_tags': sorted(regional),
        'allergen_tags': sorted(allergen),
        'occasion_tags': sorted(occasion),
    }