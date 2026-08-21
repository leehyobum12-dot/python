"""
Silver Layer: 7차원 태그 분류 (Aho-Corasick)
=============================================
- Aho-Corasick 알고리즘으로 O(N) 매칭
- 기존 enrich/classifier.py의 키워드 사전 SSOT 연동

기존 참고:
- enrich/classifier.py의 _match_keywords() 함수
- enrich/classifier.py의 CUISINE_KEYWORDS 등 키워드 사전

성능 개선:
- 기존: O(N×K) 서브스트링 매칭
- 개선: O(N+K) Aho-Corasick 자동기계

의존성:
- pyahocorasick>=2.0.0 (import ahocorasick)

수정 사항:
- ahocorasick unbound 에러 해결
- 미사용 taxonomy import 제거
"""
from __future__ import annotations

from typing import Dict, List, Optional, Set

# ─────────────── Aho-Corasick import (unbound 방지) ───────────────
try:
    import ahocorasick
    AHO_CORASICK_AVAILABLE = True
except ImportError:
    ahocorasick = None  # ← None으로 초기화하여 unbound 방지
    AHO_CORASICK_AVAILABLE = False

from ..domain.avoid_tags import generate_avoid_tags
from ..observability.logging import get_logger


logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════
# 키워드 사전 (기존 enrich/classifier.py에서 이관)
# ═══════════════════════════════════════════════════════════

CUISINE_KEYWORDS: Dict[str, List[str]] = {
    '한식': [
        '김치', '된장', '고추장', '찌개', '국', '탕', '밥', '면', '비빔',
        '볶음', '조림', '찜', '구이', '전', '적', '회', '무침', '나물',
        '김밥', '떡볶이', '순대', '어묵', '호떡', '붕어빵', '한정식',
        '백반', '정식', '해장국', '국수', '냉면', '칼국수', '수제비',
        '만두', '전골', '보쌈', '족발', '수육', '불고기', '갈비',
        '삼겹살', '돼지', '소', '닭', '오리', '흑돼지', '갈치', '몸국',
        '고기국수', '돔베', '옥돔', '성게', '전복', '소라', '보말',
        '설렁탕', '곰탕', '육개장', '감자탕', '추어탕', '순두부',
        '부대', '청국장', '콩나물', '김치찌개', '된장찌개', '고등어',
    ],
    '중식': [
        '짜장', '짬뽕', '탕수육', '마라', '양꼬치', '딤섬', '만두',
        '볶음밥', '군만두', '완탕', '유린기', '팔보채', '라조기',
        '냉채', '마라탕', '마라샹궈',
    ],
    '양식': [
        '스테이크', '파스타', '피자', '리조또', '샐러드', '브런치',
        '샌드위치', '버거', '수프', '경양식', '돈까스', '카츠',
    ],
    '일식': [
        '초밥', '스시', '사시미', '라멘', '우동', '소바', '돈카츠',
        '카츠', '덮밥', '사케', '모밀', '카레', '오코노미야키',
        '타코야키', '튀김', '텐푸라', '롤', '마끼', '카츠동', '규동',
    ],
    '디저트': [
        '케이크', '쿠키', '마카롱', '빵', '도넛', '아이스크림', '빙수',
        '카페', '커피', '라떼', '에스프레소', '스무디', '주스', '에이드',
        '차', '티', '요거트', '와플', '팬케이크', '크로플',
    ],
}

INGREDIENT_KEYWORDS: Dict[str, List[str]] = {
    '돼지': [
        '돼지', '흑돼지', '돈가스', '돈까스', '삼겹', '목살', '항정',
        '족발', '보쌈', '제육', '차돌', '근고기',
    ],
    '소': [
        '소고기', '쇠고기', '한우', '등심', '안심', '차돌', '도가니',
        '설렁', '곰탕', '육개장', '갈비', '스테이크',
    ],
    '닭': ['닭', '치킨', '통닭', '파닭', '닭갈비', '삼계', '백숙', '닭강정'],
    '양': ['양고기', '양꼬치', '램'],
    '수산물': [
        '회', '생선', '고등어', '갈치', '장어', '전복', '새우', '조개',
        '굴', '물회', '해물', '해산', '오징어', '주꾸미', '낙지', '멍게',
        '성게', '옥돔', '삼치', '방어', '돔', '아귀', '소라', '보말',
        '한치', '몸', '가리비', '홍합', '키조개', '꼬막', '연어', '참치',
        '광어', '우럭', '도미',
    ],
}

COOKING_KEYWORDS: Dict[str, List[str]] = {
    '국물': [
        '국', '찌개', '전골', '탕', '국밥', '해장국', '수프', '스프',
        '라멘', '우동', '면', '칼국수', '냉면', '막국수',
    ],
    '구이': ['구이', '바베큐', '그릴', '로스트', '스테이크', '삼겹살', '갈비', '흑돼지'],
    '볶음': ['볶음', '볶음밥', '제육볶음', '떡볶이', '라볶이'],
    '조림': ['조림', '갈치조림', '감자조림', '아구찜'],
    '찜': ['찜', '갈비찜', '아구찜', '찜닭', '닭볶음탕'],
    '튀김': ['튀김', '탕수육', '까스', '커틀릿', '프라이드', '치킨', '돈까스', '텐푸라'],
    '회·생': ['회', '사시미', '초밥', '스시', '물회', '회덮밥'],
}

NUTRIENT_KEYWORDS: Dict[str, List[str]] = {
    '탄수화물': ['죽', '밥', '면', '떡', '국수', '칼국수', '냉면', '라면', '파스타', '짜장', '짬뽕', '빵', '소바'],
    '단백질': ['돼지', '소', '닭', '양', '수산물', '두부', '계란', '콩', '고기', '생선', '해물'],
    '지방': ['튀김', '삼겹살', '갈비', '치즈', '버터', '크림', '마요네즈'],
    '식이섬유': ['채소', '해조류', '나물', '샐러드', '야채', '미역', '다시마', '김'],
}

REGIONAL_KEYWORDS: Dict[str, List[str]] = {
    '제주': [
        '흑돼지', '갈치', '전복', '소라', '성게', '한치', '옥돔',
        '메밀', '몸국', '보말', '고기국수', '돔베', '자리돔', '붕장어',
    ],
    '부산·경남': ['돼지국밥', '밀면', '꼼장어', '씨앗호떡', '회', '복국', '조개구이'],
    '전라': ['비빔밥', '콩나물국밥', '낙지', '한정식', '홍어', '보리밥', '애호박찌개'],
    '강원': ['닭갈비', '막국수', '감자옹심이', '곰치국', '초당두부', '황태'],
    '서울·경기': ['설렁탕', '갈비', '칼국수', '부대찌개', '감자탕'],
    '이북식': ['냉면', '만두', '보쌈', '평양냉면', '함흥냉면'],
    '충청': ['두부', '올갱이', '어죽', '천안호두과자'],
}

ALLERGEN_KEYWORDS: Dict[str, List[str]] = {
    '갑각류·조개류': ['새우', '게', '조개', '굴', '전복', '가리비', '홍합', '키조개', '꼬막', '대하', '랍스터', '크랩'],
    '생선·연체류': ['고등어', '갈치', '오징어', '낙지', '주꾸미', '문어', '쭈꾸미', '생선', '회', '연어', '참치', '광어', '우럭', '도미'],
    '육류': ['돼지', '소', '닭', '오리', '양', '고기'],
    '견과·땅콩': ['땅콩', '호두', '잣', '아몬드', '캐슈넛', '피칸', '마카다미아'],
    '곡류·두류': ['밀', '메밀', '콩', '대두', '면', '국수', '칼국수', '짜장', '짬뽕', '빵', '만두', '파스타', '두부', '된장', '청국장', '콩나물'],
    '유제품·알류': ['우유', '계란', '달걀', '치즈', '크림', '버터', '라떼', '요거트', '오믈렛'],
    '과일·채소류': ['복숭아', '토마토', '키위', '딸기', '사과', '멜론', '바나나', '감귤', '오렌지', '레몬'],
    '아황산류': ['아황산', '와인', '건포도'],
}

OCCASION_KEYWORDS: Dict[str, List[str]] = {
    '뷰': ['오션뷰', '바다뷰', '해변', '한옥', '야경', '루프탑', '전망', '테라스'],
    '프라이빗': ['독실', '룸', '예약제', '프라이빗'],
    '코스·파인': ['코스', '파인다이닝', '정식', '오마카세', '샤브샤브'],
    '이벤트': ['프로포즈', '상견례', '돌잔치', '회갑', '생일', '기념일', '크리스마스', '밸런타인'],
    '단체': ['뷔페', '단체석', '대관', '회식', '워크샵'],
}


class AhoCorasickMatcher:
    """
    Aho-Corasick 기반 키워드 매칭기

    기존 enrich/classifier.py의 _match_keywords() 개선:
    - O(N×K) → O(N+K) 성능 개선
    - pyahocorasick C 확장 사용
    """

    def __init__(self, keywords_dict: Dict[str, List[str]]):
        self.keywords_dict = keywords_dict
        self.automaton = None

        if AHO_CORASICK_AVAILABLE and ahocorasick is not None:
            self._build_automaton()
        else:
            logger.warning('ahocorasick_not_available_fallback_to_naive')

    def _build_automaton(self) -> None:
        """Aho-Corasick 자동기계 구축"""
        if ahocorasick is None:
            return

        self.automaton = ahocorasick.Automaton()

        for main_category, keywords in self.keywords_dict.items():
            for keyword in keywords:
                self.automaton.add_word(keyword, main_category)

        self.automaton.make_automaton()

    def match(self, text: str) -> List[str]:
        """
        텍스트에서 매칭되는 메인 카테고리 반환

        Args:
            text: 매칭할 텍스트

        Returns:
            매칭된 메인 카테고리 목록 (정렬)
        """
        if not text:
            return []

        if self.automaton is not None:
            # Aho-Corasick 매칭
            matched: Set[str] = set()
            for end_index, main_category in self.automaton.iter(text):
                matched.add(main_category)
            return sorted(matched)
        else:
            # 폴백: 단순 서브스트링 매칭
            return self._naive_match(text)

    def _naive_match(self, text: str) -> List[str]:
        """단순 서브스트링 매칭 (폴백)"""
        matched: Set[str] = set()
        text_lower = text.lower()

        for main_category, keywords in self.keywords_dict.items():
            for keyword in keywords:
                if keyword in text or keyword.lower() in text_lower:
                    matched.add(main_category)
                    break

        return sorted(matched)


class TaxonomyClassifier:
    """
    7차원 태그 분류기

    사용법:
        classifier = TaxonomyClassifier()
        tags = classifier.classify('제주 흑돼지 구이', '한식')
    """

    def __init__(self):
        self.cuisine_matcher = AhoCorasickMatcher(CUISINE_KEYWORDS)
        self.ingredient_matcher = AhoCorasickMatcher(INGREDIENT_KEYWORDS)
        self.cooking_matcher = AhoCorasickMatcher(COOKING_KEYWORDS)
        self.nutrient_matcher = AhoCorasickMatcher(NUTRIENT_KEYWORDS)
        self.regional_matcher = AhoCorasickMatcher(REGIONAL_KEYWORDS)
        self.allergen_matcher = AhoCorasickMatcher(ALLERGEN_KEYWORDS)
        self.occasion_matcher = AhoCorasickMatcher(OCCASION_KEYWORDS)

        logger.info('taxonomy_classifier_initialized')

    def classify(self, text: str, biz_type: str = '') -> Dict[str, List[str]]:
        """
        7차원 태그 분류

        Args:
            text: 분류할 텍스트 (메뉴명 또는 상호명)
            biz_type: 업종 (fallback용)

        Returns:
            7차원 태그 딕셔너리
        """
        if not text or not text.strip():
            return {
                'cuisine_tags': [],
                'nutrient_tags': [],
                'cooking_tags': [],
                'ingredient_tags': [],
                'regional_tags': [],
                'allergen_tags': [],
                'occasion_tags': [],
            }

        # 각 차원별 매칭
        cuisine_tags = self.cuisine_matcher.match(text)
        nutrient_tags = self.nutrient_matcher.match(text)
        cooking_tags = self.cooking_matcher.match(text)
        ingredient_tags = self.ingredient_matcher.match(text)
        regional_tags = self.regional_matcher.match(text)
        allergen_tags = self.allergen_matcher.match(text)
        occasion_tags = self.occasion_matcher.match(text)

        # 육류 알레르기 추가 (돼지/소/닭 감지 시)
        if any(ing in ingredient_tags for ing in ['돼지', '소', '닭']):
            if '육류' not in allergen_tags:
                allergen_tags.append('육류')

        # 기본값 처리 (한식 fallback)
        if not cuisine_tags:
            if biz_type and '한식' in biz_type:
                cuisine_tags = ['한식']

        return {
            'cuisine_tags': sorted(cuisine_tags),
            'nutrient_tags': sorted(nutrient_tags),
            'cooking_tags': sorted(cooking_tags),
            'ingredient_tags': sorted(ingredient_tags),
            'regional_tags': sorted(regional_tags),
            'allergen_tags': sorted(allergen_tags),
            'occasion_tags': sorted(occasion_tags),
        }

    def classify_and_add_avoid_tags(
        self,
        text: str,
        biz_type: str = '',
    ) -> Dict[str, List[str]]:
        """
        7차원 태그 분류 + avoid_tags 자동 생성

        Args:
            text: 분류할 텍스트
            biz_type: 업종

        Returns:
            7차원 태그 + avoid_tags 딕셔너리
        """
        tags = self.classify(text, biz_type)
        tags['avoid_tags'] = generate_avoid_tags(tags['allergen_tags'])
        return tags


# 전역 인스턴스 (싱글톤)
_classifier: Optional[TaxonomyClassifier] = None


def get_classifier() -> TaxonomyClassifier:
    """분류기 인스턴스 반환"""
    global _classifier
    if _classifier is None:
        _classifier = TaxonomyClassifier()
    return _classifier


def classify_text(text: str, biz_type: str = '') -> Dict[str, List[str]]:
    """텍스트 분류 (편의 함수)"""
    classifier = get_classifier()
    return classifier.classify(text, biz_type)