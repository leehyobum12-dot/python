# data_pipeline/enrich/ner/core/ner_pipeline.py
"""
NER 파이프라인 통합 모듈 (Phase 5)
- 공식 API 텍스트에서 메뉴 추출
- 기존 패턴 매칭 + NER 모델 병행
- 실전 데이터 로깅 (재학습용)
"""
import os
import json
import logging
from datetime import datetime
from typing import List, Dict, Optional, Tuple

try:
    from ..inference.ner_predictor import MenuNERPredictor
except ImportError:
    from inference.ner_predictor import MenuNERPredictor

BASE_DIR = os.path.dirname(os.path.abspath(__file__))  # ner/core/
NER_ROOT = os.path.dirname(BASE_DIR)                    # ner/
DATA_DIR = os.path.join(NER_ROOT, 'data')              # ner/data/

# 실전 데이터 로깅 경로 (재학습용)
REAL_DATA_DIR = os.path.join(DATA_DIR, 'real_data')
REAL_DATA_LOG_PATH = os.path.join(REAL_DATA_DIR, 'real_data_log.jsonl')

try:
    from ..inference.ner_predictor import MenuNERPredictor
except ImportError:
    from inference.ner_predictor import MenuNERPredictor

# 로거 설정
logger = logging.getLogger(__name__)

class NERMenuPipeline:
    """NER 기반 메뉴 추출 파이프라인"""
    
    def __init__(self):
        self.predictor = MenuNERPredictor()
        self.log_count = 0
        
        # 로깅 디렉토리 생성
        os.makedirs(REAL_DATA_DIR, exist_ok=True)
        
        print(f"✅ NER 파이프라인 초기화 완료")
    
    def extract_menus_from_text(
        self,
        text: str,
        store_name: str,
        source: str = 'blog'
    ) -> Dict:
        """텍스트에서 메뉴 추출 (NER + 후처리)
        
        Args:
            text: 블로그/카페/뉴스 텍스트
            store_name: 가게 이름
            source: 출처 (blog/cafe/news)
        
        Returns:
            {
                'main_menu': 대표메뉴,
                'sub_menus': [서브메뉴],
                'all_menus': [추출된 모든 메뉴],
                'confidence': 신뢰도,
            }
        """
        if not text or not text.strip():
            return {'main_menu': None, 'sub_menus': [], 'all_menus': [], 'confidence': 0.0}
        
        # NER 추출
        result = self.predictor.predict(text)
        menus = result.get('menus', [])
        
        # 메뉴 필터링 (가게 이름과 동일한 메뉴 제외)
        filtered_menus = [m for m in menus if m != store_name and len(m) >= 2]
        
        # 대표메뉴 선택 (첫 번째 추출된 메뉴)
        main_menu = filtered_menus[0] if filtered_menus else None
        
        # 서브메뉴 (대표메뉴 제외)
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
                'text': text[:500],  # 텍스트는 500자로 제한
                'store_name': store_name,
                'source': source,
                'extracted_menus': menus,
            }
            
            with open(REAL_DATA_LOG_PATH, 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
            
            self.log_count += 1
            
            # 100건마다 로깅 카운트 출력
            if self.log_count % 100 == 0:
                print(f"   📝 실전 데이터 로깅: {self.log_count}건")
        
        except Exception as e:
            logger.warning(f"실전 데이터 로깅 실패: {e}")


# ═══════════════════════════════════════════════════════════
# 기존 패턴 매칭과 NER 병행 사용
# ═══════════════════════════════════════════════════════════
class HybridMenuExtractor:
    """하이브리드 메뉴 추출기 (패턴 + NER)"""
    
    def __init__(self):
        self.ner_pipeline = NERMenuPipeline()
    
    def extract(
        self,
        text: str,
        store_name: str,
        source: str = 'blog'
    ) -> Dict:
        """하이브리드 메뉴 추출
        
        전략:
        1. NER 추출 시도
        2. NER 결과 없으면 패턴 매칭 (기존 official_enricher 로직)
        3. 신뢰도 기반 선택
        """
        # 1단계: NER 추출
        ner_result = self.ner_pipeline.extract_menus_from_text(text, store_name, source)
        
        if ner_result['main_menu']:
            return {
                'menu': ner_result['main_menu'],
                'menus': ner_result['sub_menus'],
                'source': f'ner_{source}',
                'confidence': ner_result['confidence'],
            }
        
        # 2단계: NER 결과 없으면 None 반환 (기존 패턴 매칭은 official_enricher에서 처리)
        return {
            'menu': None,
            'menus': [],
            'source': None,
            'confidence': 0.0,
        }