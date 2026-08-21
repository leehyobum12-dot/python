"""
NLP: 하이브리드 메뉴 추출기 (다차원 신뢰도)
=============================================
- NER (1차) + 로컬 LLM (2차 검증)
- 다차원 신뢰도 점수로 LLM 호출 최적화
- 가게명 필터링 강화

기존 참고:
- ner/core/hybrid_extractor.py 전체 구조
- 다차원 신뢰도 계산 로직
- 가게명 필터링

보존 사항:
- 다차원 신뢰도 계산 (화이트리스트, 근접성, 길이, 특수문자, 개수)
- 가게명 필터링 (SequenceMatcher)
- LLM 호출 최소화

개선 사항:
- 구조화 로깅
- TypedDict로 반환 타입 명시
"""
from __future__ import annotations

import asyncio
import difflib
import re
from typing import List, Dict, Optional, TypedDict

from .ner.predictor import MenuNERPredictor
from .llm.verifier import LLMVerifier
from ..domain.constants import COOKING_SUFFIXES, INGREDIENT_NOUNS
from ..settings import get_settings
from ..observability.logging import get_logger


logger = get_logger(__name__)


class HybridExtractResult(TypedDict):
    """하이브리드 추출 결과"""
    menus: List[str]
    confidence: float
    method: str


class HybridMenuExtractor:
    """
    하이브리드 메뉴 추출기

    사용법:
        extractor = HybridMenuExtractor()
        result = await extractor.extract_menus(text, store_name)
    """

    def __init__(self):
        self.settings = get_settings()
        self.ner_predictor = MenuNERPredictor()
        self.llm_verifier = LLMVerifier()
        self.llm_call_count = 0

        if self.llm_verifier.available:
            logger.info('hybrid_mode_enabled', mode='NER + LLM')
        else:
            logger.info('hybrid_mode_enabled', mode='NER only')

    # ─────────────── 다차원 신뢰도 계산 ───────────────

    def _calculate_confidence(
        self, menus: List[str], text: str, store_name: str
    ) -> float:
        """
        다차원 신뢰도 점수 계산

        기존 ner/core/hybrid_extractor.py의 _calculate_confidence() 함수 보존

        Args:
            menus: 추출된 메뉴 목록
            text: 입력 텍스트
            store_name: 가게명

        Returns:
            신뢰도 점수 (0.0~1.0)
        """
        if not menus:
            return 0.0

        best_score = 0.0
        weights = self.settings.confidence_weights

        for menu in menus:
            score = 0.0

            # 1) 화이트리스트 매칭 (조리법 or 재료)
            has_cooking = any(sfx in menu for sfx in COOKING_SUFFIXES)
            has_ingredient = any(ing in menu for ing in INGREDIENT_NOUNS)
            if has_cooking or has_ingredient:
                score += weights.get('whitelist_match', 0.30)

            # 2) 가게명 근접성 (10자 이내)
            if store_name and menu in text and store_name in text:
                menu_idx = text.find(menu)
                store_idx = text.find(store_name)
                if abs(menu_idx - store_idx) <= 10:
                    score += weights.get('store_proximity', 0.20)

            # 3) 메뉴 길이 적정성 (2~8자)
            if 2 <= len(menu) <= 8:
                score += weights.get('length_valid', 0.10)

            # 4) 특수문자 없음
            if not re.search(r'[0-9!@#$%^&*()_+=\[\]{}]', menu):
                score += weights.get('no_special_char', 0.10)

            best_score = max(best_score, score)

        # 5) 메뉴 개수 보너스
        if len(menus) >= 2:
            best_score += weights.get('multi_menu', 0.10)

        return min(best_score, 1.0)

    # ─────────────── 가게명 필터링 ───────────────

    def _is_similar_to_store(self, menu: str, store_name: str) -> bool:
        """
        메뉴가 가게명과 유사한지 확인

        기존 ner/core/hybrid_extractor.py의 _is_similar_to_store() 함수 보존

        Args:
            menu: 메뉴명
            store_name: 가게명

        Returns:
            유사하면 True
        """
        if not menu or not store_name:
            return False
        if menu == store_name:
            return True
        if menu in store_name or store_name in menu:
            return True
        similarity = difflib.SequenceMatcher(None, menu, store_name).ratio()
        return similarity >= self.settings.store_similarity_threshold

    def _filter_store_from_menus(
        self, menus: List[str], store_name: str
    ) -> List[str]:
        """
        메뉴 목록에서 가게명 제거

        Args:
            menus: 메뉴 목록
            store_name: 가게명

        Returns:
            필터링된 메뉴 목록
        """
        return [
            m for m in menus
            if m and not self._is_similar_to_store(m, store_name)
        ]

    # ─────────────── 메인 추출 ───────────────

    async def extract_menus(
        self, text: str, store_name: str
    ) -> HybridExtractResult:
        """
        하이브리드 메뉴 추출

        기존 ner/core/hybrid_extractor.py의 extract_menus() 함수 보존

        Args:
            text: 입력 텍스트
            store_name: 가게명

        Returns:
            {'menus': [...], 'confidence': float, 'method': str}
        """
        if not text or not text.strip():
            return {'menus': [], 'confidence': 0.0, 'method': 'empty'}

        # 1차: NER 추출
        ner_result = self.ner_predictor.predict(text, store_name=store_name)
        menus = ner_result.get('menus', [])

        # 가게명 필터링 + 최소 길이
        filtered_menus = [
            m for m in menus if m != store_name and len(m) >= 2
        ]

        # 다차원 신뢰도 계산
        confidence = self._calculate_confidence(
            filtered_menus, text, store_name
        )

        # 신뢰도 높으면 NER 결과 사용 (LLM 호출 안 함)
        if confidence >= self.settings.llm_verify_threshold and filtered_menus:
            return {
                'menus': filtered_menus,
                'confidence': confidence,
                'method': 'ner_only',
            }

        # 2차: 신뢰도 낮거나 결과 없으면 LLM 검증
        if self.llm_verifier.available:
            llm_menus = await self.llm_verifier.verify_menu_with_context(
                text, store_name, filtered_menus
            )
            if llm_menus is not None:
                self.llm_call_count += 1

                # '없음' 필터링 + 가게명 필터링
                llm_menus = [
                    m for m in llm_menus
                    if m and m != '없음' and m.strip()
                ]
                llm_menus = self._filter_store_from_menus(
                    llm_menus, store_name
                )

                if llm_menus:
                    return {
                        'menus': llm_menus,
                        'confidence': 0.8,
                        'method': 'llm_verified',
                    }

        # 3차: NER 결과 fallback
        return {
            'menus': filtered_menus,
            'confidence': confidence,
            'method': 'ner_fallback',
        }