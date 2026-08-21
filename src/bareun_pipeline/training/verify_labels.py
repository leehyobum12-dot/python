"""
라벨 품질 검증
================
- 잔존 쓰레기 제거
- B-MENU 없는 샘플 폐기
- 블랙리스트 검증

기존 참고:
- ner/training/verify_labels.py 전체 구조
- MENU_BLACKLIST
- verify_sample() 검증 로직

보존 사항:
- 라벨 검증 로직
- 블랙리스트
- B-MENU 비율 계산

개선 사항:
- 구조화 로깅
- 타입 안전성 강화
"""
from __future__ import annotations

from typing import Dict, Tuple

from ..observability.logging import get_logger


logger = get_logger(__name__)


# ─────────────── 잔존 쓰레기 블랙리스트 ───────────────
MENU_BLACKLIST = {
    # 기존 항목 유지
    '메뉴', '레스토랑', '식당', '카페', '가게', '장소', '분위기',
    '서비스', '가격', '위치', '주차', '영업시간', '예약', '주문',
    '비코스', '코스', '세트', '정식', '요리', '음식', '식사',
    '추천', '후기', '리뷰', '방문', '사진', '영상', '블로그',
    '본점', '분점', '지점', '호점', '센터', '마트',
    '술', '안주', '음료', '주류', '맥주', '소주',
    '오', '전', '후', '중', '때', '쯤', '여기', '저기',

    # 부사/형용사 (메뉴가 될 수 없음)
    '야무지게', '진짜', '정말', '너무', '완전', '매우', '꽤',
    '존맛', '꿀맛', '강추', '인생', '최고', '대박', '핵맛',
    '푸짐한', '깔끔한', '고소한', '바삭한', '촉촉한', '노릇하게',
    '맛있는', '맛있', '맛집', '맛집인', '맛집에서',

    # 일반 재료명 (단독 사용 시 메뉴 아님)
    '고기', '고기들', '생선', '채소', '야채', '쌀',
    '밥', '국물', '반찬', '밑반찬', '김치',

    # 실전 발견 쓰레기
    '동점', '고기동점', '단연코', '인생돈카츠',

    # 최종 보강
    '전국', '지역', '제주도', '대한민국',
    '63', '빌딩', '센터', '플라자', '몰',
    '성게', '전복', '갈치', '고등어',
    '오겹', '삼겹', '목살', '항정',
}

MIN_MENU_LENGTH = 2
MAX_MENU_LENGTH = 12


def verify_sample(sample: Dict) -> Tuple[bool, str]:
    """
    개별 샘플 검증

    기존 ner/training/verify_labels.py의 verify_sample() 함수 보존

    Args:
        sample: 검증할 샘플

    Returns:
        (통과 여부, 사유)
    """
    tokens = sample.get('tokens', [])
    labels = sample.get('labels', [])

    if not tokens or not labels or len(tokens) != len(labels):
        return False, '빈 샘플 또는 길이 불일치'

    if 'B-MENU' not in labels:
        return False, 'B-MENU 없음'

    # B-MENU 토큰 추출 및 검증
    menu_tokens = []
    for token, label in zip(tokens, labels):
        if label in ('B-MENU', 'I-MENU'):
            menu_tokens.append(token)

    menu_text = ' '.join(menu_tokens)

    # 숫자 토큰 필터 (B-MENU에 숫자가 포함되면 폐기)
    for token in menu_tokens:
        if token.strip().isdigit():
            return False, f'숫자 토큰: {token}'

    # 길이 검증
    if len(menu_text) < MIN_MENU_LENGTH or len(menu_text) > MAX_MENU_LENGTH:
        return False, f'메뉴 길이 부적합: {menu_text}'

    # 블랙리스트 검증
    if menu_text in MENU_BLACKLIST:
        return False, f'블랙리스트: {menu_text}'

    for token in menu_tokens:
        clean_token = token.strip()
        if clean_token in MENU_BLACKLIST:
            return False, f'토큰 블랙리스트: {clean_token}'

    return True, '통과'