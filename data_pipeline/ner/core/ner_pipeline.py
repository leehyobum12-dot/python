# data_pipeline/ner/core/ner_pipeline.py
"""
NER 파이프라인 통합 모듈 v6 (카테고리 분류 포함)
================================================
- NER 추출 → 검증 → 실전 데이터 로깅
- 신뢰도 기반 분기
"""
import os
import json
import logging
from datetime import datetime
from typing import List, Dict, Optional

try:
    from ..inference.ner_predictor import MenuNERPredictor
    from ...config.paths import REAL_DATA_LOG_PATH, NER_DATA_DIR
except ImportError:
    from data_pipeline.ner.inference.ner_predictor import MenuNERPredictor
    from data_pipeline.config.paths import REAL_DATA_LOG_PATH, NER_DATA_DIR

logger = logging.getLogger(__name__)


class NERMenuPipeline:
    """NER 기반 메뉴 추출 파이프라인"""

    def __init__(self):
        self.predictor = MenuNERPredictor()
        self.log_count = 0

        # 로깅 디렉토리 생성
        os.makedirs(NER_DATA_DIR, exist_ok=True)
        print(f"✅ NER 파이프라인 초기화 완료")

    def extract_menus_from_text(
        self,
        text: str,
        store_name: str,
        source: str = 'blog'
    ) -> Dict:
        """
        텍스트에서 메뉴 추출 (NER + 후처리)

        Returns:
            {
                'main_menu': 대표메뉴,
                'sub_menus': [서브메뉴],
                'all_menus': [추출된 모든 메뉴],
                'confidence': 신뢰도,
            }
        """
        if not text or not text.strip():
            return {
                'main_menu': None,
                'sub_menus': [],
                'all_menus': [],
                'confidence': 0.0,
            }

        # NER 추출
        result = self.predictor.predict(text)
        menus = result.get('menus', [])

        # 메뉴 필터링 (가게 이름과 동일한 메뉴 제외)
        filtered_menus = [
            m for m in menus if m != store_name and len(m) >= 2
        ]

        # 대표메뉴 선택
        main_menu = filtered_menus[0] if filtered_menus else None

        # 서브메뉴
        sub_menus = filtered_menus[1:6] if len(filtered_menus) > 1 else []

        # 신뢰도 계산 (메뉴 개수 기반)
        confidence = min(len(filtered_menus) * 0.3, 1.0)

        # 실전 데이터 로깅 (재학습용)
        self._log_real_data(text, store_name, source, filtered_menus)

        return {
            'main_menu': main_menu,
            'sub_menus': sub_menus,
            'all_menus': filtered_menus,
            'confidence': confidence,
        }

    def _log_real_data(
        self,
        text: str,
        store_name: str,
        source: str,
        menus: List[str]
    ) -> None:
        """실전 데이터 로깅 (재학습용)"""
        try:
            log_entry = {
                'timestamp': datetime.now().isoformat(),
                'text': text[:500],
                'store_name': store_name,
                'source': source,
                'extracted_menus': menus,
            }
            with open(REAL_DATA_LOG_PATH, 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')

            self.log_count += 1
            if self.log_count % 100 == 0:
                print(f"  📝 실전 데이터 로깅: {self.log_count}건")

        except Exception as e:
            logger.warning(f"실전 데이터 로깅 실패: {e}")