"""
실전 데이터 라벨링
===================
- real_data_log.jsonl 로드
- 쓰레기 메뉴 필터링
- BIO 태깅 변환

기존 참고:
- ner/training/label_real_data.py 전체 구조
- is_valid_menu() 쓰레기 필터링
- tokenize_and_label() BIO 태깅

보존 사항:
- 쓰레기 메뉴 필터링
- BIO 태깅 변환
- 중복 제거

개선 사항:
- 구조화 로깅
- 타입 안전성 강화
"""
from __future__ import annotations

import json
import os
import re
from typing import List, Dict, Tuple

from ..observability.logging import get_logger


logger = get_logger(__name__)


# ─────────────── 쓰레기 필터 패턴 ───────────────
SENTENCE_INDICATORS = [
    '이', '가', '을', '를', '은', '는', '에', '에서', '하고',
    '더한', '이나', '장소', '입니다', '했어요', '했습니다',
    '맛있', '추천', '주문', '시켰', '먹었', '다녀왔',
]

LOCATION_NAMES = {
    '제주', '서귀포', '제주시', '안덕', '대정', '표선', '성산',
    '조천', '한림', '애월', '중문', '노형', '연동', '함덕',
    '서울', '부산', '대구', '인천', '광주', '대전', '울산',
    '파주시', '군포시', '수원시', '고양시',
}

GENERIC_NOUNS = {
    '메뉴', '레스토랑', '식당', '카페', '가게', '장소', '분위기',
    '서비스', '가격', '위치', '주차', '영업시간', '예약', '주문',
    '비코스', '코스', '세트', '정식',
    '요리', '음식', '식사', '간식', '안주',
    '오', '전', '후', '중', '때', '쯤', '여기', '저기',
    '본점', '분점', '지점', '호점', '센터', '마트',
    '추천', '후기', '리뷰', '방문', '다녀온',
    '사진', '영상', '글', '포스팅', '블로그',
    '술', '안주', '음료', '주류', '맥주', '소주',
    '통가죽', '페파민트', '합성색소', '베이글',
}

AMBIGUOUS_SHORT = {'코스', '세트', '정식', '모듬', '특선'}

MENU_MAX_LENGTH = 15
MENU_MIN_LENGTH = 2


def load_real_data(real_data_log: str) -> List[Dict]:
    """
    실전 데이터 로드

    Args:
        real_data_log: 실전 데이터 로그 파일 경로

    Returns:
        로드된 데이터 목록
    """
    records: List[Dict] = []
    seen_texts = set()  # 중복 제거

    if not os.path.exists(real_data_log):
        logger.warning('real_data_log_not_found', path=real_data_log)
        return []

    with open(real_data_log, 'r', encoding='utf-8') as f:
        for line in f:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                # 중복 텍스트 제거 (첫 100자 기준)
                text_key = record.get('text', '')[:100]
                if text_key in seen_texts:
                    continue
                seen_texts.add(text_key)
                records.append(record)
            except json.JSONDecodeError:
                continue

    logger.info('real_data_loaded', count=len(records))
    return records


def is_valid_menu(menu: str, store_name: str) -> Tuple[bool, str]:
    """
    메뉴 유효성 검증 (쓰레기 필터링)

    기존 ner/training/label_real_data.py의 is_valid_menu() 함수 보존

    Args:
        menu: 메뉴명
        store_name: 가게명

    Returns:
        (유효 여부, 사유)
    """
    if not menu:
        return False, '빈 문자열'

    menu = menu.strip()

    # 길이 검증
    if len(menu) < MENU_MIN_LENGTH:
        return False, '너무 짧음'
    if len(menu) > MENU_MAX_LENGTH:
        return False, '너무 김 (문장 의심)'

    # 일반명사 필터
    if menu in GENERIC_NOUNS:
        return False, f'일반명사: {menu}'

    # 모호한 짧은 단어 필터
    if menu in AMBIGUOUS_SHORT:
        return False, f'모호한 일반명사: {menu}'

    # 문장 감지 (조사/어미 포함)
    for indicator in SENTENCE_INDICATORS:
        if indicator in menu:
            return False, f'문장 의심: {indicator}'

    # 가게명 포함 감지
    if store_name:
        store_norm = re.sub(r'[^가-힣a-zA-Z0-9]', '', store_name).lower()
        menu_norm = re.sub(r'[^가-힣a-zA-Z0-9]', '', menu).lower()
        if len(store_norm) >= 3 and store_norm[:3] in menu_norm:
            return False, '가게명 포함'

    # 지역명 포함 감지
    for loc in LOCATION_NAMES:
        if loc in menu:
            return False, f'지역명 포함: {loc}'

    # 숫자만 있는 경우
    if re.match(r'^[\d\s.,]+$', menu):
        return False, '숫자만'

    # 특수문자 과다
    if re.search(r'[^\uAC00-\uD7A3a-zA-Z0-9\s]', menu):
        return False, '특수문자'

    return True, '유효'


def tokenize_and_label(
    text: str,
    store_name: str,
    valid_menus: List[str]
) -> Dict:
    """
    공백 기준 토큰화 + BIO 태깅

    기존 ner/training/label_real_data.py의 tokenize_and_label() 함수 보존

    Args:
        text: 입력 텍스트
        store_name: 가게명
        valid_menus: 유효한 메뉴 목록

    Returns:
        {'tokens': [...], 'labels': [...]}
    """
    tokens = text.split()
    labels = ['O'] * len(tokens)

    # 가게명 태깅 (B-STORE)
    if store_name:
        for i, token in enumerate(tokens):
            if store_name in token:
                labels[i] = 'B-STORE'

    # 메뉴 태깅 (B-MENU, I-MENU)
    for menu in valid_menus:
        menu_tokens = menu.split()
        for i in range(len(tokens) - len(menu_tokens) + 1):
            if tokens[i:i + len(menu_tokens)] == menu_tokens:
                labels[i] = 'B-MENU'
                for j in range(1, len(menu_tokens)):
                    labels[i + j] = 'I-MENU'

    return {'tokens': tokens, 'labels': labels}