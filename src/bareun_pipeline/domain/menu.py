"""
도메인: 메뉴 유효성 검증 및 정규화 (SSOT)
==========================================
- menu_extract.py와 enrich/menu.py에서 공통 사용
- 메뉴명 정규화, 유효성 검증, 중복 제거
"""

from __future__ import annotations

import re

# 메뉴명으로 부적합한 패턴
INVALID_MENU_PATTERNS: list[str] = [
    r'^[0-9,]+원$',  # 가격만 있는 경우
    r'^\d+인분$',  # 인분수만 있는 경우
    r'^[가-힣]{1}$',  # 한글 한 글자
    r'^(세트|식사|음료|메뉴|요리|코스|기본|추가)$',
    r'^(서비스|무료|제공)$',
]

# 가게명이 메뉴명으로 잘못 추출되는 임계값
STORE_NAME_IN_MENU_THRESHOLD = 0.7


def is_valid_menu_name(
    menu_name: str,
    store_name: str | None = None,
) -> bool:
    """
    메뉴명 유효성 검증

    Args:
        menu_name: 검증할 메뉴명
        store_name: 가게명 (가게명 중복 방지용)

    Returns:
        유효하면 True, 아니면 False

    사용처:
        - silver/enrich/menu.py: API 검색 결과 필터링
        - silver/menu_extract.py: NER/LLM 추출 결과 필터링
    """
    if not menu_name or not isinstance(menu_name, str):
        return False

    menu = menu_name.strip()

    # 길이 검증
    if len(menu) < 2 or len(menu) > 50:
        return False

    # 패턴 검증
    for pattern in INVALID_MENU_PATTERNS:
        if re.match(pattern, menu):
            return False

    # 유효 문자 비율 검증
    valid_chars = len(re.findall(r'[\uAC00-\uD7A3a-zA-Z0-9]', menu))
    if valid_chars / len(menu) < 0.5:
        return False

    # 가게명 중복 방지
    if store_name and len(store_name) >= 2:
        from difflib import SequenceMatcher

        ratio = SequenceMatcher(None, menu, store_name).ratio()
        if ratio >= STORE_NAME_IN_MENU_THRESHOLD:
            return False

    return True


def normalize_menu_name(menu_name: str) -> str:
    """
    메뉴명 정규화 (공백 정리, 특수문자 제거)

    Args:
        menu_name: 원본 메뉴명

    Returns:
        정규화된 메뉴명
    """
    if not menu_name:
        return ''

    # 앞뒤 공백 제거
    menu = menu_name.strip()

    # 연속 공백을 하나로
    menu = re.sub(r'\s+', ' ', menu)

    # 괄호 안의 불필요한 정보 제거 (예: "(소)", "(대)")
    menu = re.sub(r'\s*\((소|중|대|1인|2인|세트)\)\s*', ' ', menu)

    return menu.strip()


def deduplicate_menus(menus: list[str]) -> list[str]:
    """
    메뉴 목록 중복 제거 (정규화 후)

    Args:
        menus: 메뉴명 리스트

    Returns:
        중복 제거된 메뉴명 리스트 (정렬됨)
    """
    normalized = [normalize_menu_name(m) for m in menus]
    unique = sorted(set(normalized))
    return [m for m in unique if m]  # 빈 문자열 제거
