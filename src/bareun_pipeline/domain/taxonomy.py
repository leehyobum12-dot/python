"""
도메인: Taxonomy Registry (7차원 카테고리 SSOT)
=================================================
- 기존 config/taxonomy/ 폴더 전체를 하나의 모듈로 통합
- O(1) 역추적 _reverse_map으로 DB 인덱싱 최적화
- Aho-Corasick 알고리즘과 연동 가능한 인터페이스 제공

기존 참고:
- config/taxonomy/base.py: TaxonomyRegistry 클래스
- config/taxonomy/cuisine.py: CUISINE_DATA
- config/taxonomy/ingredient.py: INGREDIENT_DATA
- config/taxonomy/cooking.py: COOKING_DATA
- config/taxonomy/nutrient.py: NUTRIENT_DATA
- config/taxonomy/regional.py: REGIONAL_DATA
- config/taxonomy/allergen.py: ALLERGEN_DATA
- config/taxonomy/occasion.py: OCCASION_DATA

설계 원칙:
- TaxonomyRegistry 클래스는 기존 로직을 그대로 보존
- 각 taxonomy 데이터는 기존 파일에서 이관
- ALL_TAXONOMIES 딕셔너리로 통합 접근 제공
"""

from __future__ import annotations


class TaxonomyRegistry:
    """
    모든 Taxonomy 도메인의 공통 베이스 클래스.

    기존 config/taxonomy/base.py의 TaxonomyRegistry를 그대로 보존합니다.
    O(1) 역추적 _reverse_map으로 DB 인덱싱 최적화.

    Attributes:
        _data: 메인 카테고리 → 서브 카테고리 목록 매핑
        _reverse_map: 서브 카테고리 → 메인 카테고리 역매핑 (O(1) 조회)
    """

    def __init__(self, data: dict[str, list[str]]):
        self._data = data
        self._reverse_map = self._build_reverse_map()

    def _build_reverse_map(self) -> dict[str, str]:
        """역매핑 구축 (서브 카테고리 → 메인 카테고리)"""
        r_map: dict[str, str] = {}
        for main_cat, sub_cats in self._data.items():
            for sub in sub_cats:
                r_map[sub] = main_cat
        return r_map

    def get_subcategories(self, main_category: str) -> list[str]:
        """메인 카테고리의 서브 카테고리 목록 반환"""
        return self._data.get(main_category, [])

    def validate(self, main_category: str, sub_category: str) -> bool:
        """메인-서브 카테고리 쌍 유효성 검증"""
        return sub_category in self._data.get(main_category, [])

    def get_main_category(self, sub_category: str) -> str:
        """서브 카테고리로 메인 카테고리 조회 (O(1))"""
        return self._reverse_map.get(sub_category, 'UNKNOWN')

    def get_all_keywords(self) -> set[str]:
        """모든 서브 카테고리 키워드를 Set으로 반환 (부분 매칭용)"""
        return set(self._reverse_map.keys())

    def get_all_main_categories(self) -> list[str]:
        """모든 메인 카테고리 목록 반환"""
        return list(self._data.keys())

    def match(self, text: str) -> list[str]:
        """
        텍스트에서 매칭되는 메인 카테고리 목록 반환

        주의: 이 메서드는 O(N×K) 단순 서브스트링 매칭입니다.
        대량 처리 시에는 silver/classify.py의 AhoCorasickMatcher를 사용하세요.

        Args:
            text: 매칭할 텍스트

        Returns:
            매칭된 메인 카테고리 목록
        """
        matched: set[str] = set()
        for keyword, main_cat in self._reverse_map.items():
            if keyword in text:
                matched.add(main_cat)
        return list(matched)

    def match_with_keywords(self, text: str) -> dict[str, list[str]]:
        """
        텍스트에서 매칭된 메인 카테고리별 매칭된 키워드 반환

        Args:
            text: 매칭할 텍스트

        Returns:
            {메인 카테고리: [매칭된 키워드 목록]}
        """
        result: dict[str, list[str]] = {}
        for keyword, main_cat in self._reverse_map.items():
            if keyword in text:
                if main_cat not in result:
                    result[main_cat] = []
                result[main_cat].append(keyword)
        return result


# ═══════════════════════════════════════════════════════════
# 7차원 Taxonomy 데이터 (기존 config/taxonomy/에서 이관)
# ═══════════════════════════════════════════════════════════

# ─────────────── 1. 요리 타입 (Cuisine) ───────────────
# 기존 config/taxonomy/cuisine.py에서 이관
CUISINE_DATA: dict[str, list[str]] = {
    '한식': ['일반', '분식', '궁중', '향토', '사찰'],
    '중식': ['짜장', '짬뽕', '볶음밥', '탕수육'],
    '양식': ['스테이크', '파스타', '피자', '버거', '샐러드'],
    '일식': ['초밥', '회', '라멘', '우동', '돈카츠'],
    '디저트': ['카페', '베이커리', '아이스크림', '전통다과'],
}

# ─────────────── 2. 재료 (Ingredient) ───────────────
# 기존 config/taxonomy/ingredient.py에서 이관
INGREDIENT_DATA: dict[str, list[str]] = {
    '돼지': ['돼지고기', '삼겹살', '목살', '갈비', '제육', '불고기', '차돌', '근고기', '흑돼지'],
    '소': ['소고기', '안심', '등심', '채끝', '살치살', '토시살', '육회'],
    '닭': ['닭고기', '치킨', '닭갈비', '삼계탕', '닭볶음탕'],
    '양': ['양고기', '양갈비', '램'],
    '수산물': [
        '갈치',
        '고등어',
        '전복',
        '조개',
        '홍합',
        '굴',
        '게',
        '가리비',
        '연어',
        '참치',
        '광어',
        '우럭',
        '도미',
        '새우',
        '오징어',
        '문어',
        '낙지',
        '쭈꾸미',
        '성게',
        '해삼',
        '소라',
        '꼬막',
        '키조개',
        '몸',
        '보말',
        '한치',
        '옥돔',
    ],
    '채소': [
        '감자',
        '고구마',
        '호박',
        '가지',
        '오이',
        '당근',
        '양파',
        '파',
        '부추',
        '미나리',
        '시금치',
        '배추',
        '열무',
        '무',
        '콩나물',
        '숙주',
        '버섯',
        '표고',
        '느타리',
        '양송이',
        '팽이',
        '새송이',
    ],
    '곡류': ['쌀', '보리', '밀', '메밀', '잡곡', '국수', '칼국수', '소면'],
    '유제품': ['치즈', '크림', '버터', '우유'],
}

# ─────────────── 3. 조리법 (Cooking) ───────────────
# 기존 config/taxonomy/cooking.py에서 이관
COOKING_DATA: dict[str, list[str]] = {
    '국물': ['국', '찌개', '전골', '탕', '국밥', '해장국'],
    '구이': ['구이', '바베큐', '그릴', '로스트'],
    '볶음': ['볶음', '볶음밥', '제육볶음', '볶이'],
    '조림': ['조림', '갈치조림'],
    '찜': ['찜', '갈비찜', '아구찜', '찜닭'],
    '튀김': ['튀김', '강정', '탕수', '프라이'],
    '회·생': ['회', '사시미', '초밥', '스시'],
    '면': ['면', '국수', '냉면', '라면', '우동', '파스타', '짜장', '짬뽕'],
    '밥': ['밥', '비빔밥', '덮밥', '김밥', '볶음밥'],
    '전·부침': ['전', '부침', '빈대떡', '파전'],
}

# ─────────────── 4. 영양소 (Nutrient) ───────────────
# 기존 config/taxonomy/nutrient.py에서 이관
NUTRIENT_DATA: dict[str, list[str]] = {
    '탄수화물': ['죽', '밥', '면', '떡', '국수', '칼국수'],
    '단백질': ['돼지', '소', '닭', '양', '수산물', '두부', '계란'],
    '지방': ['튀김', '삼겹살', '갈비', '치즈', '버터'],
    '식이섬유': ['채소', '해조류', '나물', '샐러드'],
}

# ─────────────── 5. 지역 특색 (Regional) ───────────────
# 기존 config/taxonomy/regional.py에서 이관
REGIONAL_DATA: dict[str, list[str]] = {
    '제주': ['흑돼지', '갈치', '전복', '소라', '성게', '한치', '옥돔', '메밀', '몸국', '보말'],
    '부산·경남': ['돼지국밥', '밀면', '꼼장어', '회', '씨앗호떡'],
    '전라': ['비빔밥', '콩나물국밥', '낙지', '한정식', '홍어'],
    '강원': ['닭갈비', '막국수', '감자옹심이', '곰치국', '초당두부'],
    '서울·경기': ['설렁탕', '갈비', '칼국수', '부대찌개'],
    '이북식': ['냉면', '만두', '보쌈'],
    '충청': ['두부', '올갱이', '어죽'],
}

# ─────────────── 6. 알레르기 (Allergen) ───────────────
# 기존 config/taxonomy/allergen.py에서 이관
ALLERGEN_DATA: dict[str, list[str]] = {
    '갑각류·조개류': ['새우', '게', '조개', '굴', '전복'],
    '생선·연체류': ['고등어', '갈치', '오징어', '낙지', '주꾸미'],
    '육류': ['돼지', '소', '닭'],
    '견과·땅콩': ['땅콩', '호두', '잣'],
    '곡류·두류': ['밀', '메밀', '콩'],
    '유제품·알류': ['우유', '계란'],
    '과일·채소류': ['복숭아', '토마토', '키위'],
    '아황산류': ['아황산'],
}

# ─────────────── 7. 상황/분위기 (Occasion) ───────────────
# 기존 config/taxonomy/occasion.py에서 이관
OCCASION_DATA: dict[str, list[str]] = {
    '뷰': ['오션뷰', '한옥', '야경', '루프탑'],
    '프라이빗': ['독실', '룸', '예약제'],
    '코스·파인': ['코스', '파인다이닝', '정식'],
    '이벤트': ['프로포즈', '상견례', '돌잔치', '회갑'],
    '단체': ['뷔페', '단체석'],
}


# ═══════════════════════════════════════════════════════════
# Taxonomy 인스턴스 (싱글톤)
# ═══════════════════════════════════════════════════════════

CuisineTaxonomy = TaxonomyRegistry(CUISINE_DATA)
IngredientTaxonomy = TaxonomyRegistry(INGREDIENT_DATA)
CookingTaxonomy = TaxonomyRegistry(COOKING_DATA)
NutrientTaxonomy = TaxonomyRegistry(NUTRIENT_DATA)
RegionalTaxonomy = TaxonomyRegistry(REGIONAL_DATA)
AllergenTaxonomy = TaxonomyRegistry(ALLERGEN_DATA)
OccasionTaxonomy = TaxonomyRegistry(OCCASION_DATA)

# 기존 config/taxonomy/__init__.py의 ALL_TAXONOMIES 보존
ALL_TAXONOMIES: dict[str, TaxonomyRegistry] = {
    'cuisine': CuisineTaxonomy,
    'ingredient': IngredientTaxonomy,
    'cooking': CookingTaxonomy,
    'nutrient': NutrientTaxonomy,
    'regional': RegionalTaxonomy,
    'allergen': AllergenTaxonomy,
    'occasion': OccasionTaxonomy,
}


def get_taxonomy(name: str) -> TaxonomyRegistry | None:
    """이름으로 Taxonomy 인스턴스 조회"""
    return ALL_TAXONOMIES.get(name)


def get_all_taxonomy_names() -> list[str]:
    """모든 Taxonomy 이름 목록 반환"""
    return list(ALL_TAXONOMIES.keys())
