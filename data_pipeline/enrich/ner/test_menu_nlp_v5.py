# data_pipeline/enrich/menu_extractor.py (v6.0)
"""
Qwen 3.8 NLP 능력을 활용한 정교한 메뉴 추출 모듈 (v6.0)
- 가게 이름 필터링 강화
- 업종-메뉴 일치 검증 강화
- 신뢰도 임계값 상향
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
    confidence: float
    source_api: str
    context: str = ""


# ─────────────── 확장된 블랙리스트 ───────────────
NON_FOOD_WORDS = {
    # 지명/장소
    '화서역', '서울역', '부산역', '동문시장', '모슬포항', '제주공항',
    '시장', '역', '공원', '학교', '병원', '회사', '백화점', '아울렛',
    
    # 가격/수량
    '만원이면', '만원', '천원', '원', '가격', '비용', '할인', '무료',
    '일차', '이차', '삼차', '번째', '회전', '빠른회전',
    
    # 동사/부사
    '직접', '예약', '주문', '방문', '추천', '강추', '꼭', '완전',
    '이제야', '드디어', '마침내', '결국', '사실', '진짜', '정말',
    '조리하여', '시켜서', '말아', '메뉴를', '점심을',
    
    # 일반 명사
    '여행', '미식회', '지역사회', '구호전', '추진전', '탐나는전',
    '년전통', '분들이라면', '예정이라면', '음식이라면', '주식회',
    '차애전', '노모어피자', '우정해장국',
    
    # 조사/어미
    '입니다', '했어요', '습니다', '합니다', '됩니다',
}

FOOD_INDICATORS = {
    '구이', '찌개', '탕', '국밥', '비빔밥', '냉면', '회', '초밥', '전골',
    '덮밥', '해장국', '국수', '라면', '파스타', '피자', '스테이크',
    '볶음', '조림', '찜', '전', '죽', '빵', '케이크', '튀김', '샐러드',
    '무침', '말이', '쌈', '롤', '꼬치', '바비큐', '그릴',
    '고기', '돼지', '소', '닭', '오리', '양', '해물', '해산물',
    '야채', '채소', '김치', '된장', '고추장', '간장', '소금', '후추',
    '마늘', '생강', '파', '양파', '당근', '감자', '고구마', '호박',
    '세트', '정식', '코스', '단품', '메뉴', '요리', '음식', '식사',
}


# ─────────────── 가게 이름 필터링 (v6.0 핵심) ───────────────
def is_store_name_not_menu(menu: str, store_name: str) -> Tuple[bool, str]:
    """메뉴가 가게 이름인지 판단
    
    Returns:
        (is_store_name, reason)
    """
    if not menu or not store_name:
        return False, ""
    
    # 정규화
    normalized_menu = re.sub(r'[^가-힣a-zA-Z0-9]', '', menu).lower()
    normalized_store = re.sub(r'[^가-힣a-zA-Z0-9]', '', store_name).lower()
    
    # 완전 일치
    if normalized_menu == normalized_store:
        return True, "가게 이름과 완전 일치"
    
    # 메뉴가 가게 이름에 포함됨
    if normalized_menu in normalized_store:
        return True, f"메뉴 '{menu}'가 가게 이름에 포함됨"
    
    # 가게 이름이 메뉴에 포함됨
    if normalized_store in normalized_menu:
        return True, f"가게 이름이 메뉴 '{menu}'에 포함됨"
    
    # 가게 이름의 주요 키워드와 일치
    store_keywords = set(re.findall(r'[가-힣]{2,}', store_name))
    menu_keywords = set(re.findall(r'[가-힣]{2,}', menu))
    
    # 2개 이상 키워드 일치 시 가게 이름으로 판단
    common_keywords = store_keywords & menu_keywords
    if len(common_keywords) >= 2:
        return True, f"가게 키워드 일치: {common_keywords}"
    
    return False, ""


# ─────────────── 음식 이름 판별 ───────────────
def is_likely_food_name(text: str) -> Tuple[bool, float, str]:
    """텍스트가 음식 이름인지 판단"""
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
    
    if food_score >= 1:
        confidence = min(0.5 + food_score * 0.2, 1.0)
        return True, confidence, f"음식 지표: {matched_indicators}"
    
    if 2 <= len(text) <= 10 and re.match(r'^[가-힣]+$', text):
        return True, 0.3, "한글 단어 (음식 가능성)"
    
    return False, 0.0, "음식 아님"


# ─────────────── 업종-메뉴 일치 검증 (v6.0 확장) ───────────────
CATEGORY_MENU_MAP = {
    '한식': {
        'expected': ['김치찌개', '된장찌개', '비빔밥', '불고기', '갈비', '잡채', '전', '국', '탕', '곰탕', '설렁탕'],
        'unexpected': ['짜장면', '짬뽕', '초밥', '파스타', '피자'],
    },
    '중식': {
        'expected': ['짜장면', '짬뽕', '탕수육', '마라탕', '마라샹궈', '볶음밥', '군만두', '양장피', '팔보채'],
        'unexpected': ['김치찌개', '비빔밥', '초밥', '파스타', '돼지국밥', '해장국'],
    },
    '일식': {
        'expected': ['초밥', '회', '라멘', '우동', '돈카츠', '덮밥', '오뎅', '연어초밥', '참치초밥'],
        'unexpected': ['김치찌개', '짜장면', '파스타', '돼지국밥'],
    },
    '양식': {
        'expected': ['파스타', '피자', '스테이크', '리조또', '샐러드', '햄버거', '뇨끼'],
        'unexpected': ['김치찌개', '짜장면', '초밥', '돼지국밥'],
    },
    '흑돼지': {
        'expected': ['흑돼지구이', '근고기', '돈마호크', '삼겹살', '목살', '항정살', '흑돼지전'],
        'unexpected': ['몸국', '김치찌개', '짜장면', '해장국'],
    },
    '국수': {
        'expected': ['고기국수', '비빔국수', '멸치국수', '칼국수', '막국수', '열무국수'],
        'unexpected': ['짜장면', '초밥', '피자', '돼지국밥'],
    },
    '해장국': {
        'expected': ['해장국', '몸국', '육개장', '선지해장국', '내장탕', '고사리해장국'],
        'unexpected': ['짜장면', '초밥', '파스타', '피자'],
    },
    '국밥': {
        'expected': ['돼지국밥', '소고기국밥', '순대국밥', '내장국밥', '따로국밥'],
        'unexpected': ['짜장면', '초밥', '파스타', '피자'],
    },
    '카페': {
        'expected': ['아메리카노', '라떼', '케이크', '치즈케이크', '빵', '쿠키', '스콘'],
        'unexpected': ['돼지국밥', '김치찌개', '해장국', '짜장면'],
    },
}


def validate_menu_by_category(menu: str, biz_type: str, store_name: str) -> Tuple[bool, float, str]:
    """업종과 메뉴가 일치하는지 검증"""
    if not biz_type:
        return True, 0.5, "업종 정보 없음 (중립)"
    
    biz_lower = biz_type.lower()
    
    for category, config in CATEGORY_MENU_MAP.items():
        if category in biz_lower:
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
    score = 0.5
    
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


# ─────────────── 통합 메뉴 추출 (v6.0) ───────────────
def extract_menus_with_nlp_validation(
    text: str,
    store_name: str,
    biz_type: str = "",
    source_api: str = "blog"
) -> List[MenuCandidate]:
    """NLP 검증을 포함한 메뉴 추출 (v6.0)"""
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
            
            # ✅ v6.0: 가게 이름 필터링 (가장 먼저 체크)
            is_store_name, store_reason = is_store_name_not_menu(cleaned, store_name)
            if is_store_name:
                continue  # 가게 이름이면 메뉴 후보에서 제외
            
            # 음식 이름 판별
            is_food, food_confidence, food_reason = is_likely_food_name(cleaned)
            if not is_food:
                continue
            
            # 업종-메뉴 일치 검증
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
    store_name: str,
    min_confidence: float = 0.6,  # ✅ v6.0: 임계값 상향 (0.5 → 0.6)
    max_menus: int = 6
) -> Tuple[Optional[str], List[str]]:
    """최적의 대표메뉴 + 서브메뉴 선택 (v6.0)"""
    if not all_candidates:
        return None, []
    
    # 신뢰도 필터링
    valid_candidates = [c for c in all_candidates if c.confidence >= min_confidence]
    
    if not valid_candidates:
        valid_candidates = [c for c in all_candidates if c.confidence >= 0.4]
    
    if not valid_candidates:
        return None, []
    
    # ✅ v6.0: 서브메뉴에서 가게 이름 필터링
    filtered_candidates = []
    for c in valid_candidates:
        is_store_name, _ = is_store_name_not_menu(c.text, store_name)
        if not is_store_name:
            filtered_candidates.append(c)
    
    if not filtered_candidates:
        return None, []
    
    # 빈도수 계산
    menu_counter = Counter([c.text for c in filtered_candidates])
    
    # 가장 많이 언급된 메뉴 = 대표메뉴
    main_menu = menu_counter.most_common(1)[0][0]
    
    # 나머지 = 서브메뉴
    seen = {main_menu}
    sub_menus = []
    
    for candidate in filtered_candidates:
        if candidate.text not in seen:
            seen.add(candidate.text)
            sub_menus.append(candidate.text)
            if len(sub_menus) >= max_menus - 1:
                break
    
    return main_menu, sub_menus