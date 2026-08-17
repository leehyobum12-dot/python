# data_pipeline/enrich/menu_extractor.py
"""
Qwen 3.8 NLP 능력을 활용한 정교한 메뉴 추출 모듈 (v5.0)
- 3단계 검증 파이프라인
- 의미 기반 음식 이름 판별
- 업종-메뉴 일치 검증
- 신뢰도 점수 시스템
"""

import re
from typing import List, Tuple, Optional, Dict, Set
from collections import Counter
from dataclasses import dataclass


@dataclass
class MenuCandidate:
    """메뉴 후보"""
    text: str
    pattern: str
    confidence: float  # 0.0 ~ 1.0
    source_api: str
    context: str = ""


# ─────────────── 1단계: 확장된 블랙리스트 ───────────────
NON_FOOD_WORDS = {
    # 지명/장소
    '화서역', '서울역', '부산역', '동문시장', '모슬포항', '제주공항',
    '시장', '역', '공원', '학교', '병원', '회사', '백화점', '아울렛',
    
    # 가격/수량 표현
    '만원이면', '만원', '천원', '원', '가격', '비용', '할인', '무료',
    '일차', '이차', '삼차', '번째', '회전', '빠른회전',
    
    # 동사/부사
    '직접', '예약', '주문', '방문', '추천', '강추', '꼭', '완전',
    '이제야', '드디어', '마침내', '결국', '사실', '진짜', '정말',
    
    # 일반 명사
    '여행', '미식회', '지역사회', '구호전', '추진전', '탐나는전',
    '년전통', '분들이라면', '예정이라면', '음식이라면', '주식회',
    
    # 조사/어미
    '입니다', '했어요', '습니다', '합니다', '됩니다', '됩니다',
}

# 음식 이름에 자주 등장하는 단어 (화이트리스트)
FOOD_INDICATORS = {
    # 조리법
    '구이', '찌개', '탕', '국밥', '비빔밥', '냉면', '회', '초밥', '전골',
    '덮밥', '해장국', '국수', '라면', '파스타', '피자', '스테이크',
    '볶음', '조림', '찜', '전', '죽', '빵', '케이크', '튀김', '샐러드',
    '무침', '말이', '쌈', '롤', '꼬치', '꼬치구이', '바비큐', '그릴',
    
    # 재료
    '고기', '돼지', '소', '닭', '오리', '양', '해물', '해산물',
    '야채', '채소', '김치', '된장', '고추장', '간장', '소금', '후추',
    '마늘', '생강', '파', '양파', '당근', '감자', '고구마', '호박',
    
    # 음식 카테고리
    '세트', '정식', '코스', '단품', '메뉴', '요리', '음식', '식사',
}


# ─────────────── 2단계: 음식 이름 판별 (의미 기반) ───────────────
def is_likely_food_name(text: str) -> Tuple[bool, float, str]:
    """텍스트가 음식 이름인지 판단
    
    Returns:
        (is_food, confidence, reason)
    """
    if not text or len(text) < 2:
        return False, 0.0, "너무 짧음"
    
    if len(text) > 20:
        return False, 0.0, "너무 김"
    
    # 블랙리스트 체크
    if text in NON_FOOD_WORDS:
        return False, 0.0, f"블랙리스트: {text}"
    
    # 부분 블랙리스트 체크
    for non_food in NON_FOOD_WORDS:
        if non_food in text:
            return False, 0.0, f"비음식 단어 포함: {non_food}"
    
    # 음식 지표 체크
    food_score = 0
    matched_indicators = []
    
    for indicator in FOOD_INDICATORS:
        if indicator in text:
            food_score += 1
            matched_indicators.append(indicator)
    
    # 음식 지표가 1개 이상 있으면 음식일 가능성 높음
    if food_score >= 1:
        confidence = min(0.5 + food_score * 0.2, 1.0)
        return True, confidence, f"음식 지표: {matched_indicators}"
    
    # 음식 지표가 없어도 한글 2~10자면 음식일 가능성 있음
    if 2 <= len(text) <= 10 and re.match(r'^[가-힣]+$', text):
        return True, 0.3, "한글 단어 (음식 가능성)"
    
    return False, 0.0, "음식 아님"


# ─────────────── 3단계: 업종-메뉴 일치 검증 ───────────────
CATEGORY_MENU_MAP = {
    # 한식
    '한식': {
        'expected': ['김치찌개', '된장찌개', '비빔밥', '불고기', '갈비', '잡채', '전', '국', '탕'],
        'unexpected': ['짜장면', '짬뽕', '초밥', '파스타', '피자'],
    },
    # 중식
    '중식': {
        'expected': ['짜장면', '짬뽕', '탕수육', '마라탕', '마라샹궈', '볶음밥', '군만두'],
        'unexpected': ['김치찌개', '비빔밥', '초밥', '파스타'],
    },
    # 일식
    '일식': {
        'expected': ['초밥', '회', '라멘', '우동', '돈카츠', '덮밥', '오뎅'],
        'unexpected': ['김치찌개', '짜장면', '파스타'],
    },
    # 양식
    '양식': {
        'expected': ['파스타', '피자', '스테이크', '리조또', '샐러드', '햄버거'],
        'unexpected': ['김치찌개', '짜장면', '초밥'],
    },
    # 흑돼지/고기집
    '흑돼지': {
        'expected': ['흑돼지구이', '근고기', '돈마호크', '삼겹살', '목살', '항정살'],
        'unexpected': ['몸국', '김치찌개', '짜장면'],
    },
    # 국수집
    '국수': {
        'expected': ['고기국수', '비빔국수', '멸치국수', '칼국수', '막국수'],
        'unexpected': ['짜장면', '초밥', '피자'],
    },
    # 해장국
    '해장국': {
        'expected': ['해장국', '몸국', '육개장', '선지해장국', '내장탕'],
        'unexpected': ['짜장면', '초밥', '파스타'],
    },
}


def validate_menu_by_category(menu: str, biz_type: str, store_name: str) -> Tuple[bool, float, str]:
    """업종과 메뉴가 일치하는지 검증
    
    Returns:
        (is_valid, confidence, reason)
    """
    if not biz_type:
        return True, 0.5, "업종 정보 없음 (중립)"
    
    # 업종 키워드 추출
    biz_lower = biz_type.lower()
    
    for category, config in CATEGORY_MENU_MAP.items():
        if category in biz_lower or any(kw in biz_lower for kw in [category]):
            # 예상 메뉴 체크
            for expected in config['expected']:
                if expected in menu:
                    return True, 0.9, f"업종 일치: {category}"
            
            # 비예상 메뉴 체크
            for unexpected in config['unexpected']:
                if unexpected in menu:
                    return False, 0.1, f"업종 불일치: {category}인데 {menu}"
    
    return True, 0.5, "업종 검증 불가 (중립)"


# ─────────────── 문맥 분석 ───────────────
def analyze_context_score(text: str, menu: str) -> float:
    """메뉴 주변 문맥을 분석해서 신뢰도 점수 계산"""
    score = 0.5  # 기본 점수
    
    # 높은 신뢰도 문맥
    high_confidence_patterns = [
        r'대표\s*메뉴',
        r'추천\s*메뉴',
        r'시그니처',
        r'메인\s*메뉴',
        r'인기\s*메뉴',
        r'꼭\s*먹어',
        r'필수\s*주문',
    ]
    
    for pattern in high_confidence_patterns:
        if re.search(pattern, text):
            score += 0.3
            break
    
    # 낮은 신뢰도 문맥
    low_confidence_patterns = [
        r'만원이면',
        r'직접',
        r'예약',
        r'주문\s*후',
        r'방문\s*전',
    ]
    
    for pattern in low_confidence_patterns:
        if re.search(pattern, text):
            score -= 0.3
            break
    
    return max(0.0, min(1.0, score))


# ─────────────── 통합 메뉴 추출 ───────────────
def extract_menus_with_nlp_validation(
    text: str,
    store_name: str,
    biz_type: str = "",
    source_api: str = "blog"
) -> List[MenuCandidate]:
    """NLP 검증을 포함한 메뉴 추출
    
    Returns:
        List of MenuCandidate (신뢰도 순으로 정렬)
    """
    # 1단계: 패턴 매칭
    candidates = []
    
    patterns = [
        (r'대표\s*메뉴\s*[:는]\s*([가-힣]{2,15})(?:\s|$|,)', '대표메뉴'),
        (r'추천\s*메뉴\s*[:는]\s*([가-힣]{2,15})(?:\s|$|,)', '추천메뉴'),
        (r'시그니처\s*[:는]\s*([가-힣]{2,15})(?:\s|$|,)', '시그니처'),
        (r'메인\s*메뉴\s*[:는]\s*([가-힣]{2,15})(?:\s|$|,)', '메인메뉴'),
        (r'([가-힣]{2,12})(?:를|을)?\s*(?:먹|드시)', '먹다'),
        (r'([가-힣]{2,12})\s*(?:진짜|정말|너무)\s*맛있', '맛있다'),
        (r'(흑돼지\s*구이|고기\s*국수|갈치\s*조림|성게\s*미역국|보말\s*칼국수|전복\s*죽|몸국|돔베고기)', '제주특화'),
        (r'([가-힣]{2,12}(?:구이|찌개|탕|국밥|비빔밥|냉면|회|초밥|전골|덮밥|해장국|국수|라면|파스타|피자|스테이크|볶음|조림|찜|전|죽|빵|케이크))', '일반메뉴'),
    ]
    
    seen_menus = set()
    
    for pattern, pattern_name in patterns:
        matches = re.findall(pattern, text)
        for match in matches:
            cleaned = match.strip()
            if cleaned in seen_menus:
                continue
            seen_menus.add(cleaned)
            
            # 2단계: 음식 이름 판별
            is_food, food_confidence, food_reason = is_likely_food_name(cleaned)
            if not is_food:
                continue
            
            # 3단계: 업종-메뉴 일치 검증
            is_valid_category, category_confidence, category_reason = validate_menu_by_category(
                cleaned, biz_type, store_name
            )
            if not is_valid_category:
                continue
            
            # 문맥 분석
            context_score = analyze_context_score(text, cleaned)
            
            # 최종 신뢰도 계산
            final_confidence = (food_confidence * 0.4 + 
                               category_confidence * 0.3 + 
                               context_score * 0.3)
            
            candidates.append(MenuCandidate(
                text=cleaned,
                pattern=pattern_name,
                confidence=final_confidence,
                source_api=source_api,
                context=text[:100],
            ))
    
    # 신뢰도 순으로 정렬
    candidates.sort(key=lambda x: x.confidence, reverse=True)
    
    return candidates


def select_best_menus(
    all_candidates: List[MenuCandidate],
    min_confidence: float = 0.5,
    max_menus: int = 6
) -> Tuple[Optional[str], List[str]]:
    """최적의 대표메뉴 + 서브메뉴 선택
    
    Returns:
        (main_menu, sub_menus)
    """
    if not all_candidates:
        return None, []
    
    # 신뢰도 필터링
    valid_candidates = [c for c in all_candidates if c.confidence >= min_confidence]
    
    if not valid_candidates:
        # 신뢰도 기준을 낮춰서 재시도
        valid_candidates = [c for c in all_candidates if c.confidence >= 0.3]
    
    if not valid_candidates:
        return None, []
    
    # 빈도수 계산
    menu_counter = Counter([c.text for c in valid_candidates])
    
    # 가장 많이 언급된 메뉴 = 대표메뉴
    main_menu = menu_counter.most_common(1)[0][0]
    
    # 나머지 = 서브메뉴 (신뢰도 순으로 정렬)
    seen = {main_menu}
    sub_menus = []
    
    for candidate in valid_candidates:
        if candidate.text not in seen:
            seen.add(candidate.text)
            sub_menus.append(candidate.text)
            if len(sub_menus) >= max_menus - 1:
                break
    
    return main_menu, sub_menus