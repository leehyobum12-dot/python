"""
NLP: NER 후처리 모듈 (8단계 다층 검증)
========================================
- 블랙리스트 + 화이트리스트 이중 검증
- 가게명 필터링 (store_name 기반)
- 지역 접두사 정규화 (제주갈치조림 → 갈치조림)
- 동사형 어미 필터링 (치밥하기 등)
- 2글자 이하 재료 단독 메뉴 방지

기존 참고:
- ner/inference/ner_postprocessor.py 전체 구조
- 8단계 다층 검증 로직
- POSTPOSITIONS 조사 목록

보존 사항:
- 8단계 다층 검증
- 조사 제거 (보호 접미사 확인)
- 지역 접두사 정규화
- 가게명 필터링

개선 사항:
- NERPostprocessor 클래스로 캡슐화
- 구조화 로깅
- 타입 안전성 강화
"""
from __future__ import annotations

import re
from typing import List, Optional

from ...settings import get_settings
from ...domain.constants import (
    COOKING_SUFFIXES,
    INGREDIENT_NOUNS,
    LOCATION_BLACKLIST,
    SLANG_BLACKLIST,
    TIME_QUANTITY_BLACKLIST,
    PROTECTED_SUFFIXES,
    LOCATION_PREFIXES,
    VERB_ENDINGS,
)


# 숫자/특수문자 패턴
NUMBER_SPECIAL_PATTERN = re.compile(
    r'^[\d\s\.,\-\+\(\)\[\]\{\}\/\\:;!?@#$%^&*_=<>]+$'
)
LOCATION_NUMBER_PATTERN = re.compile(r'\d+번길|\d+층|\d+m|\d+만원|\d+원')

# 조사 목록 (긴 것부터 정렬하여 매칭)
POSTPOSITIONS = sorted(
    ['에서', '에게', '한테', '께', '에게서',
     '를', '을', '이', '가', '의',
     '에', '으로', '로', '와', '과', '랑', '하고',
     '도', '만', '까지', '부터', '조차', '마저', '나마',
     '은', '는'],
    key=len, reverse=True
)


class NERPostprocessor:
    """
    NER 후처리기 (8단계 다층 검증)

    사용법:
        processor = NERPostprocessor()
        cleaned = processor.process(['제주갈치조림'], 'menu', '제주맛집')
    """

    def __init__(self):
        self.settings = get_settings()

    # ─────────────── 기본 정제 ───────────────

    def _contains_special_chars(self, text: str) -> bool:
        """특수문자 포함 여부 (한글/영문/숫자/공백 제외)"""
        cleaned = re.sub(r'[\uAC00-\uD7A3a-zA-Z0-9\s]', '', text)
        return len(cleaned) > 0

    def _strip_location_prefix(self, text: str) -> str:
        """지역 접두사 제거 (제주갈치조림 → 갈치조림)"""
        for prefix in sorted(LOCATION_PREFIXES, key=len, reverse=True):
            if text.startswith(prefix) and len(text) > len(prefix):
                remainder = text[len(prefix):]
                if len(remainder) >= self.settings.menu_min_length:
                    return remainder
        return text

    def _is_protected_suffix(self, text: str) -> bool:
        """보호 접미사 여부 (조사 제거 방지)"""
        return any(text.endswith(suffix) for suffix in PROTECTED_SUFFIXES)

    # ─────────────── 화이트리스트 검증 ───────────────

    def _has_cooking_suffix(self, text: str) -> bool:
        """조리법 접미사 포함 여부"""
        return any(suffix in text for suffix in COOKING_SUFFIXES)

    def _has_ingredient(self, text: str) -> bool:
        """재료 명사 포함 여부"""
        return any(ingredient in text for ingredient in INGREDIENT_NOUNS)

    def _has_verb_ending(self, text: str) -> bool:
        """동사형 어미 포함 여부 (치밥하기 등)"""
        return any(text.endswith(ending) for ending in VERB_ENDINGS)

    def _is_store_name(self, text: str, store_name: str) -> bool:
        """가게명 여부 판정"""
        if not store_name:
            return False
        # 완전 일치
        if text == store_name:
            return True
        # 포함 관계
        if text in store_name or store_name in text:
            return True
        # 핵심 단어 추출 (네/의 제거)
        store_core = re.sub(r'(네|의)', '', store_name)
        text_core = re.sub(r'(네|의)', '', text)
        if len(store_core) >= 3 and store_core == text_core:
            return True
        return False

    def _is_valid_menu(self, text: str, store_name: str = '') -> bool:
        """
        메뉴로 유효한지 판단 (8단계 다층 검증)

        기존 ner/inference/ner_postprocessor.py의 _is_valid_menu() 함수 보존

        Args:
            text: 검증할 텍스트
            store_name: 가게명

        Returns:
            유효하면 True
        """
        if not text or len(text) < self.settings.menu_min_length:
            return False

        # Stage 1: 블랙리스트 체크
        if text in SLANG_BLACKLIST:
            return False
        if text in LOCATION_BLACKLIST:
            return False
        if text in TIME_QUANTITY_BLACKLIST:
            return False

        # Stage 2: 숫자/특수문자만 있는 경우
        if NUMBER_SPECIAL_PATTERN.match(text):
            return False

        # Stage 3: 특수문자 포함 체크
        if self._contains_special_chars(text):
            return False

        # Stage 4: 주소/전화번호 패턴
        if LOCATION_NUMBER_PATTERN.search(text):
            return False

        # Stage 5: 길이 체크
        if len(text) > self.settings.menu_max_length:
            return False

        # Stage 6: 가게명 필터링
        if store_name and self._is_store_name(text, store_name):
            return False

        # Stage 7: 동사형 어미 필터링
        if self._has_verb_ending(text):
            return False

        # Stage 8: 화이트리스트 검증
        has_cooking = self._has_cooking_suffix(text)
        has_ingredient = self._has_ingredient(text)

        # 조리법과 재료 중 하나라도 포함해야 메뉴로 인정
        if not has_cooking and not has_ingredient:
            return False

        # Stage 8b: 2글자 이하 재료 단독 메뉴 방지
        # 조리법 없이 재료만 있는 경우, 3글자 이상이어야 함
        if not has_cooking and has_ingredient and len(text) < 3:
            return False

        return True

    # ─────────────── 엔티티 정제 ───────────────

    def clean_entity(self, text: str) -> Optional[str]:
        """
        엔티티 정제 (특수문자/조사 제거 + 지역 접두사 정규화)

        기존 ner/inference/ner_postprocessor.py의 clean_entity() 함수 보존

        Args:
            text: 정제할 텍스트

        Returns:
            정제된 텍스트 (유효하지 않으면 None)
        """
        if not text:
            return None

        # 1. 특수문자 제거 (앞뒤)
        text = re.sub(r'[,.\s!?~…\[\]\(\)\{\}<>"\'`]+$', '', text)
        text = re.sub(r'^[,.\s!?~…\[\]\(\)\{\}<>"\'`]+', '', text)
        text = text.strip()

        # 2. 조사 제거 (긴 것부터, 보호 접미사 확인)
        for post in POSTPOSITIONS:
            if text.endswith(post) and len(text) > len(post):
                candidate = text[:-len(post)]
                if not self._is_protected_suffix(text):
                    text = candidate
                    break

        text = text.strip()

        # 3. 지역 접두사 제거 (제주갈치조림 → 갈치조림)
        text = self._strip_location_prefix(text)

        # 4. 최종 길이 체크
        if len(text) < self.settings.menu_min_length:
            return None

        return text

    # ─────────────── 메인 파이프라인 ───────────────

    def process(
        self,
        entities: List[str],
        entity_type: str = 'menu',
        store_name: str = ''
    ) -> List[str]:
        """
        전체 후처리 파이프라인

        기존 ner/inference/ner_postprocessor.py의 process() 함수 보존

        Args:
            entities: NER 추출 엔티티 목록
            entity_type: 'menu' 또는 'store'
            store_name: 가게명 (메뉴 필터링용)

        Returns:
            정제된 엔티티 목록
        """
        if not entities:
            return []

        # 1. 각 엔티티 정제
        cleaned = [self.clean_entity(e) for e in entities]

        # 2. None 제거 + 유효성 검증
        if entity_type == 'menu':
            cleaned = [
                e for e in cleaned
                if e is not None and self._is_valid_menu(e, store_name)
            ]
        else:
            # 가게명: 지역명만 필터링
            cleaned = [
                e for e in cleaned
                if e is not None and e not in LOCATION_BLACKLIST
            ]

        # 3. 중복 제거 (순서 유지)
        seen = set()
        unique = []
        for e in cleaned:
            if e not in seen:
                seen.add(e)
                unique.append(e)

        return unique