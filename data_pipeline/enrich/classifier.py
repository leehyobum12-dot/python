# data_pipeline/enrich/classifier.py
"""
7차원 메뉴 태깅 엔진 v4 (키워드 매칭 + SSOT 연동)
- 메뉴명을 입력받아 7가지 차원의 카테고리 태그 반환
- TaxonomyRegistry의 데이터와 키워드 매칭 로직 결합
"""

from typing import List, Dict, Set

try:
    from ..config.taxonomy import (
        CuisineTaxonomy, IngredientTaxonomy, CookingTaxonomy,
        NutrientTaxonomy, RegionalTaxonomy, AllergenTaxonomy,
        OccasionTaxonomy,
    )
except ImportError:
    from data_pipeline.config.taxonomy import (
        CuisineTaxonomy, IngredientTaxonomy, CookingTaxonomy,
        NutrientTaxonomy, RegionalTaxonomy, AllergenTaxonomy,
        OccasionTaxonomy,
    )

# ─────────────── 키워드 매칭 사전 (SSOT 기반 확장) ───────────────
# TaxonomyRegistry의 서브카테고리만으로는 구체적인 메뉴명 매칭이 불가능하므로,
# 메인 카테고리별 키워드를 정의하여 부분 매칭 수행.
# 추후 config/taxonomy/ 파일들로 이관 예정.

CUISINE_KEYWORDS = {
    '한식': ['김치', '된장', '고추장', '찌개', '국', '탕', '밥', '면', '비빔', '볶음', '조림', '찜', '구이', '전', '적', '회', '무침', '나물', '김밥', '떡볶이', '순대', '어묵', '호떡', '붕어빵', '한정식', '백반', '정식', '해장국', '국수', '냉면', '칼국수', '수제비', '만두', '전골', '보쌈', '족발', '수육', '불고기', '갈비', '삼겹살', '돼지', '소', '닭', '오리', '흑돼지', '갈치', '몸국', '고기국수', '돔베', '옥돔', '성게', '전복', '소라', '보말', '설렁탕', '곰탕', '육개장', '감자탕', '추어탕', '순두부', '부대', '청국장', '콩나물', '김치찌개', '된장찌개', '고등어', '조림'],
    '중식': ['짜장', '짬뽕', '탕수육', '마라', '양꼬치', '딤섬', '만두', '볶음밥', '군만두', '완탕', '유린기', '팔보채', '라조기', '냉채', '마라탕', '마라샹궈'],
    '양식': ['스테이크', '파스타', '피자', '리조또', '샐러드', '브런치', '샌드위치', '버거', '수프', '경양식', '돈까스', '카츠'],
    '일식': ['초밥', '스시', '사시미', '라멘', '우동', '소바', '돈카츠', '카츠', '덮밥', '사케', '모밀', '카레', '오코노미야키', '타코야키', '튀김', '텐푸라', '회', '사시미', '롤', '마끼', '카츠동', '규동'],
    '디저트': ['케이크', '쿠키', '마카롱', '빵', '도넛', '아이스크림', '빙수', '카페', '커피', '라떼', '에스프레소', '스무디', '주스', '에이드', '차', '티', '요거트', '와플', '팬케이크', '크로플'],
}

INGREDIENT_KEYWORDS = {
    '돼지': ['돼지', '흑돼지', '돈가스', '돈까스', '삼겹', '목살', '항정', '족발', '보쌈', '제육', '차돌', '근고기'],
    '소': ['소고기', '쇠고기', '한우', '등심', '안심', '차돌', '도가니', '설렁', '곰탕', '육개장', '갈비', '스테이크'],
    '닭': ['닭', '치킨', '통닭', '파닭', '닭갈비', '삼계', '백숙', '닭강정'],
    '양': ['양고기', '양꼬치', '램'],
    '수산물': ['회', '생선', '고등어', '갈치', '장어', '전복', '새우', '조개', '굴', '물회', '해물', '해산', '오징어', '주꾸미', '낙지', '멍게', '성게', '옥돔', '삼치', '방어', '돔', '아귀', '소라', '보말', '한치', '몸', '가리비', '홍합', '키조개', '꼬막', '연어', '참치', '광어', '우럭', '도미'],
}

COOKING_KEYWORDS = {
    '국물': ['국', '찌개', '전골', '탕', '국밥', '해장국', '수프', '스프', '라멘', '우동', '면', '칼국수', '냉면', '막국수'],
    '구이': ['구이', '바베큐', '그릴', '로스트', '스테이크', '삼겹살', '갈비', '흑돼지'],
    '볶음': ['볶음', '볶음밥', '제육볶음', '떡볶이', '라볶이'],
    '조림': ['조림', '갈치조림', '감자조림', '아구찜'], # 아구찜은 찜이지만 조림류로 분류되기도 함
    '찜': ['찜', '갈비찜', '아구찜', '찜닭', '닭볶음탕'],
    '튀김': ['튀김', '탕수육', '까스', '커틀릿', '프라이드', '치킨', '돈까스', '텐푸라'],
    '회·생': ['회', '사시미', '초밥', '스시', '물회', '회덮밥'],
}

NUTRIENT_KEYWORDS = {
    '탄수화물': ['죽', '밥', '면', '떡', '국수', '칼국수', '냉면', '라면', '파스타', '짜장', '짬뽕', '빵', 'うどん', '소바'],
    '단백질': ['돼지', '소', '닭', '양', '수산물', '두부', '계란', '콩', '고기', '생선', '해물'],
    '지방': ['튀김', '삼겹살', '갈비', '치즈', '버터', '크림', '마요네즈'],
    '식이섬유': ['채소', '해조류', '나물', '샐러드', '야채', '미역', '다시마', '김'],
}

REGIONAL_KEYWORDS = {
    '제주': ['흑돼지', '갈치', '전복', '소라', '성게', '한치', '옥돔', '메밀', '몸국', '보말', '고기국수', '돔베', '자리돔', '붕장어'],
    '부산·경남': ['돼지국밥', '밀면', '꼼장어', '씨앗호떡', '회', '복국', '조개구이'],
    '전라': ['비빔밥', '콩나물국밥', '낙지', '한정식', '홍어', '보리밥', '애호박찌개'],
    '강원': ['닭갈비', '막국수', '감자옹심이', '곰치국', '초당두부', '황태'],
    '서울·경기': ['설렁탕', '갈비', '칼국수', '부대찌개', '감자탕'],
    '이북식': ['냉면', '만두', '보쌈', '평양냉면', '함흥냉면'],
    '충청': ['두부', '올갱이', '어죽', '천안호두과자'],
}

ALLERGEN_KEYWORDS = {
    '갑각류·조개류': ['새우', '게', '조개', '굴', '전복', '가리비', '홍합', '키조개', '꼬막', '대하', '랍스터', '크랩'],
    '생선·연체류': ['고등어', '갈치', '오징어', '낙지', '주꾸미', '문어', '쭈꾸미', '생선', '회', '연어', '참치', '광어', '우럭', '도미'],
    '육류': ['돼지', '소', '닭', '오리', '양', '고기'],
    '견과·땅콩': ['땅콩', '호두', '잣', '아몬드', '캐슈넛', '피칸', '마카다미아'],
    '곡류·두류': ['밀', '메밀', '콩', '대두', '면', '국수', '칼국수', '짜장', '짬뽕', '빵', '만두', '파스타', '두부', '된장', '청국장', '콩나물'],
    '유제품·알류': ['우유', '계란', '달걀', '치즈', '크림', '버터', '라떼', '요거트', '오믈렛'],
    '과일·채소류': ['복숭아', '토마토', '키위', '딸기', '사과', '멜론', '바나나', '감귤', '오렌지', '레몬'],
    '아황산류': ['아황산', '와인', '건포도'],
}

OCCASION_KEYWORDS = {
    '뷰': ['오션뷰', '바다뷰', '해변', '한옥', '야경', '루프탑', '전망', '테라스'],
    '프라이빗': ['독실', '룸', '예약제', '프라이빗', '個室'],
    '코스·파인': ['코스', '파인다이닝', '정식', '오마카세', '샤브샤브'],
    '이벤트': ['프로포즈', '상견례', '돌잔치', '회갑', '생일', '기념일', '크리스마스', '밸런타인'],
    '단체': ['뷔페', '단체석', '대관', '회식', '워크샵'],
}


def _match_keywords(keywords_dict: Dict[str, List[str]], text: str) -> List[str]:
    """텍스트에서 키워드 매칭되는 메인 카테고리 목록 반환"""
    matched = set()
    text_lower = text.lower()
    for main_cat, keywords in keywords_dict.items():
        for kw in keywords:
            if kw in text or kw.lower() in text_lower:
                matched.add(main_cat)
                break
    return sorted(list(matched))


def classify(menu_text: str, biz_type: str = '') -> Dict[str, List[str]]:
    """
    7차원 태그 분류 (키워드 매칭 + SSOT 연동)
    
    Args:
        menu_text: 메뉴 텍스트 (쉼표 구분 가능)
        biz_type: 업종 분류 (Optional, 현재 미사용)
    
    Returns:
        7차원 태그 딕셔너리
    """
    text = menu_text or ''
    
    if not text.strip():
        return {
            'cuisine_tags': [],
            'nutrient_tags': [],
            'cooking_tags': [],
            'ingredient_tags': [],
            'regional_tags': [],
            'allergen_tags': [],
            'occasion_tags': [],
        }
    
    # 1. 키워드 매칭 (구체적인 메뉴명 → 메인 카테고리)
    cuisine_tags = _match_keywords(CUISINE_KEYWORDS, text)
    nutrient_tags = _match_keywords(NUTRIENT_KEYWORDS, text)
    cooking_tags = _match_keywords(COOKING_KEYWORDS, text)
    ingredient_tags = _match_keywords(INGREDIENT_KEYWORDS, text)
    regional_tags = _match_keywords(REGIONAL_KEYWORDS, text)
    allergen_tags = _match_keywords(ALLERGEN_KEYWORDS, text)
    occasion_tags = _match_keywords(OCCASION_KEYWORDS, text)
    
    # 2. 육류 알레르기 추가 (돼지/소/닭 감지 시)
    if any(ing in ingredient_tags for ing in ['돼지', '소', '닭']):
        if '육류' not in allergen_tags:
            allergen_tags.append('육류')
    
    # 3. 기본값 처리 (한식 fallback 등)
    if not cuisine_tags:
        # 업종 기반 fallback 또는 '한식' 기본값
        if biz_type and '한식' in biz_type:
            cuisine_tags = ['한식']
        else:
            # 키워드 매칭 실패 시, TaxonomyRegistry의 match()로 재시도 (서브카테고리 매칭)
            cuisine_tags = CuisineTaxonomy.match(text)
    
    return {
        'cuisine_tags': sorted(cuisine_tags),
        'nutrient_tags': sorted(nutrient_tags),
        'cooking_tags': sorted(cooking_tags),
        'ingredient_tags': sorted(ingredient_tags),
        'regional_tags': sorted(regional_tags),
        'allergen_tags': sorted(allergen_tags),
        'occasion_tags': sorted(occasion_tags),
    }


# ─────────────── 단독 실행 (테스트) ───────────────

if __name__ == '__main__':
    print("=" * 60)
    print("🧪 7차원 메뉴 분류기 테스트 (v4 - 키워드 매칭)")
    print("=" * 60)
    
    test_cases = [
        ('김치찌개', '한식'),
        ('제주 흑돼지 구이', '한식'),
        ('부산 밀면', '한식'),
        ('새우튀김', '일식'),
        ('삼겹살', '한식'),
        ('비빔밥', '한식'),
        ('마라탕', '중식'),
        ('파스타', '양식'),
    ]
    
    for menu, biz in test_cases:
        result = classify(menu, biz)
        print(f"\n메뉴: {menu}")
        for key, tags in result.items():
            if tags:
                print(f"  {key}: {tags}")