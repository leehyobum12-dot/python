# data_pipeline/enrich/ner/core/hybrid_extractor.py
"""
하이브리드 메뉴 추출기 (NER + 로컬 LLM)
- 1차: NER 모델로 빠른 추출
- 2차: 의심 케이스만 로컬 LLM으로 검증
"""
import os
import json
import asyncio
from typing import List, Dict, Optional

# ✅ 상대 임포트 (core/에서 inference/ 접근)
try:
    from ..inference.ner_predictor import MenuNERPredictor
    from .llm_verifier import LLMVerifier
except ImportError:
    from inference.ner_predictor import MenuNERPredictor
    from llm_verifier import LLMVerifier

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# LLM 검증 임계값 (이 신뢰도 이하만 LLM 검증)
LLM_VERIFY_THRESHOLD = 0.6


class HybridMenuExtractor:
    """하이브리드 메뉴 추출기"""
    
    def __init__(self):
        self.ner_predictor = MenuNERPredictor()
        self.llm_verifier = LLMVerifier()
        self.llm_call_count = 0
        
        if self.llm_verifier.available:
            print(f"✅ 하이브리드 모드: NER + 로컬 LLM")
        else:
            print(f"⚠️ NER 전용 모드 (로컬 LLM 사용 불가)")
    
    async def extract_menus(self, text: str, store_name: str) -> Dict:
        """하이브리드 메뉴 추출"""
        if not text or not text.strip():
            return {'menus': [], 'confidence': 0.0, 'method': 'empty'}
        
        # 1차: NER 추출
        ner_result = self.ner_predictor.predict(text)
        menus = ner_result.get('menus', [])
        
        # 메뉴 필터링 (가게명과 동일하면 제외)
        filtered_menus = [m for m in menus if m != store_name and len(m) >= 2]
        
        # 신뢰도 계산 (메뉴 개수 기반)
        confidence = min(len(filtered_menus) * 0.3, 1.0)
        
        # 신뢰도 높으면 NER 결과 사용
        if confidence >= LLM_VERIFY_THRESHOLD and filtered_menus:
            return {
                'menus': filtered_menus,
                'confidence': confidence,
                'method': 'ner_only',
            }
        
        # 2차: 신뢰도 낮거나 결과 없으면 LLM 검증
        if self.llm_verifier.available:
            llm_menus = await self.llm_verifier.verify_menu(text, store_name)
            if llm_menus is not None:
                self.llm_call_count += 1
                return {
                    'menus': llm_menus,
                    'confidence': 0.8,
                    'method': 'llm_verified',
                }
        
        # 3차: NER 결과 반환 (fallback)
        return {
            'menus': filtered_menus,
            'confidence': confidence,
            'method': 'ner_fallback',
        }


# ─────────────── 단독 실행 (테스트) ───────────────
async def test_hybrid():
    print("=" * 60)
    print("🧪 하이브리드 메뉴 추출기 테스트")
    print("=" * 60)
    
    extractor = HybridMenuExtractor()
    
    test_cases = [
        ("올래국수에서 고기국수를 먹었어요. 진짜 맛있었어요", "올래국수"),
        ("우진해장국 몸국 먹고 왔는데 존맛", "우진해장국"),
        ("셰프와흑돼지에서 흑돼지구이랑 된장찌개 시켰어요", "셰프와흑돼지"),
        ("파주시 지역화폐 가맹점 알아보기", "고보배국밥"),  # 오추출 케이스
    ]
    
    for text, store_name in test_cases:
        result = await extractor.extract_menus(text, store_name)
        print(f"\n가게: {store_name}")
        print(f"텍스트: {text}")
        print(f"메뉴: {result['menus']}")
        print(f"신뢰도: {result['confidence']}")
        print(f"방법: {result['method']}")
    
    print(f"\n📊 LLM 호출 횟수: {extractor.llm_call_count}")


if __name__ == '__main__':
    asyncio.run(test_hybrid())