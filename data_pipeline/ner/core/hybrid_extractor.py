# data_pipeline/ner/core/hybrid_extractor.py
"""
하이브리드 메뉴 추출기 v3 (다차원 신뢰도)
=========================================
- NER (1차) + 로컬 LLM (2차 검증)
- 다차원 신뢰도 점수로 LLM 호출 최적화
- 가게명 필터링 강화
"""
import os
import asyncio
import difflib
import re
from typing import List, Dict, Optional

try:
    from ..inference.ner_predictor import MenuNERPredictor
    from .llm_verifier import LLMVerifier
    from ...config.constants import COOKING_SUFFIXES, INGREDIENT_NOUNS
    from ...config.thresholds import (
        LLM_VERIFY_THRESHOLD, STORE_SIMILARITY_THRESHOLD,
        CONFIDENCE_WEIGHTS
    )
except ImportError:
    from data_pipeline.ner.inference.ner_predictor import MenuNERPredictor
    from data_pipeline.ner.core.llm_verifier import LLMVerifier
    from data_pipeline.config.constants import COOKING_SUFFIXES, INGREDIENT_NOUNS
    from data_pipeline.config.thresholds import (
        LLM_VERIFY_THRESHOLD, STORE_SIMILARITY_THRESHOLD,
        CONFIDENCE_WEIGHTS
    )


class HybridMenuExtractor:
    """하이브리드 메뉴 추출기 v3"""

    def __init__(self):
        self.ner_predictor = MenuNERPredictor()
        self.llm_verifier = LLMVerifier()
        self.llm_call_count = 0

        if self.llm_verifier.available:
            print(f"✅ 하이브리드 모드: NER + 로컬 LLM")
        else:
            print(f"⚠️ NER 전용 모드 (로컬 LLM 사용 불가)")

    # ─────────────── 다차원 신뢰도 계산 ───────────────

    def _calculate_confidence(
        self, menus: List[str], text: str, store_name: str
    ) -> float:
        """
        다차원 신뢰도 점수 계산

        기존: confidence = min(len(menus) * 0.3, 1.0)
        수정: 화이트리스트 + 근접성 + 길이 + 특수문자 + 개수 보너스
        """
        if not menus:
            return 0.0

        best_score = 0.0

        for menu in menus:
            score = 0.0

            # 1) 화이트리스트 매칭 (조리법 or 재료)
            has_cooking = any(sfx in menu for sfx in COOKING_SUFFIXES)
            has_ingredient = any(ing in menu for ing in INGREDIENT_NOUNS)
            if has_cooking or has_ingredient:
                score += CONFIDENCE_WEIGHTS['whitelist_match']

            # 2) 가게명 근접성 (10자 이내)
            if store_name and menu in text and store_name in text:
                menu_idx = text.find(menu)
                store_idx = text.find(store_name)
                if abs(menu_idx - store_idx) <= 10:
                    score += CONFIDENCE_WEIGHTS['store_proximity']

            # 3) 메뉴 길이 적정성 (2~8자)
            if 2 <= len(menu) <= 8:
                score += CONFIDENCE_WEIGHTS['length_valid']

            # 4) 특수문자 없음
            if not re.search(r'[0-9!@#$%^&*()_+=\[\]{}]', menu):
                score += CONFIDENCE_WEIGHTS['no_special_char']

            best_score = max(best_score, score)

        # 5) 메뉴 개수 보너스
        if len(menus) >= 2:
            best_score += CONFIDENCE_WEIGHTS['multi_menu']

        return min(best_score, 1.0)

    # ─────────────── 가게명 필터링 ───────────────

    def _is_similar_to_store(self, menu: str, store_name: str) -> bool:
        """메뉴가 가게명과 유사한지 확인"""
        if not menu or not store_name:
            return False
        if menu == store_name:
            return True
        if menu in store_name or store_name in menu:
            return True
        similarity = difflib.SequenceMatcher(None, menu, store_name).ratio()
        return similarity >= STORE_SIMILARITY_THRESHOLD

    def _filter_store_from_menus(
        self, menus: List[str], store_name: str
    ) -> List[str]:
        """메뉴 목록에서 가게명 제거"""
        return [
            m for m in menus
            if m and not self._is_similar_to_store(m, store_name)
        ]

    # ─────────────── 메인 추출 ───────────────

    async def extract_menus(self, text: str, store_name: str) -> Dict:
        """하이브리드 메뉴 추출 v3"""
        if not text or not text.strip():
            return {'menus': [], 'confidence': 0.0, 'method': 'empty'}

        # 1차: NER 추출
        ner_result = self.ner_predictor.predict(text)
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
        if confidence >= LLM_VERIFY_THRESHOLD and filtered_menus:
            return {
                'menus': filtered_menus,
                'confidence': confidence,
                'method': 'ner_only',
            }

        # 2차: 신뢰도 낮거나 결과 없으면 LLM 검증
        if self.llm_verifier.available:
            llm_menus = await self.llm_verifier.verify_menu(
                text, store_name
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


# ─────────────── 단독 실행 (테스트) ───────────────

async def test_hybrid():
    print("=" * 60)
    print("🧪 하이브리드 메뉴 추출기 v3 테스트")
    print("=" * 60)

    extractor = HybridMenuExtractor()

    test_cases = [
        ("올래국수에서 고기국수를 먹었어요. 진짜 맛있었어요", "올래국수"),
        ("우진해장국 몸국 먹고 왔는데 존맛", "우진해장국"),
        ("셰프와흑돼지에서 흑돼지구이랑 된장찌개 시켰어요", "셰프와흑돼지"),
        ("제주 여행 첫 끼로 갈치조림 먹었어요", "제주맛집"),
        ("파주시 지역화폐 가맹점 알아보기", "고보배국밥"),
        ("제주 여행 다녀왔어요", "올래국수"),
    ]

    for text, store_name in test_cases:
        result = await extractor.extract_menus(text, store_name)
        print(f"\n가게: {store_name}")
        print(f"텍스트: {text}")
        print(f"메뉴: {result['menus']}")
        print(f"신뢰도: {result['confidence']:.2f}")
        print(f"방법: {result['method']}")

    print(f"\n📊 LLM 호출 횟수: {extractor.llm_call_count}")
    print(f"📊 총 테스트: {len(test_cases)}건")
    print(f"📊 LLM 호출 비율: {extractor.llm_call_count}/{len(test_cases)}")


if __name__ == '__main__':
    asyncio.run(test_hybrid())