# data_pipeline/enrich/ner/inference/ner_predictor.py
"""
NER 모델 추론 스크립트 (Phase 4 개선 버전)
- 후처리 모듈 통합
"""
import os
import json
import torch
from transformers import AutoModelForTokenClassification, AutoTokenizer

# ✅ 수정: 상대/절대 임포트 분기
try:
    from .ner_postprocessor import NERPostprocessor
except ImportError:
    from ner_postprocessor import NERPostprocessor

BASE_DIR = os.path.dirname(os.path.abspath(__file__))  # ner/inference/
NER_ROOT = os.path.dirname(BASE_DIR)                    # ner/
MODEL_DIR = os.path.join(NER_ROOT, 'models', 'ner_model')  # ner/models/ner_model

class MenuNERPredictor:
    def __init__(self, model_path: str = None):
        # ✅ model_path 기본값을 올바른 경로로
        if model_path is None:
            model_path = MODEL_DIR
        
        # ✅ 모델 존재 여부 확인
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"모델 경로 없음: {model_path}")
        
        self.tokenizer = AutoTokenizer.from_pretrained(model_path)
        self.model = AutoModelForTokenClassification.from_pretrained(model_path)
        self.model.eval()
        
        # 라벨 정보 로드
        label_info_path = os.path.join(model_path, 'label_info.json')
        with open(label_info_path, 'r', encoding='utf-8') as f:
            label_info = json.load(f)
        self.id2label = {int(k): v for k, v in label_info['id2label'].items()}
        
        # 후처리기 초기화
        self.postprocessor = NERPostprocessor()
        
        print(f"✅ NER 모델 로드 완료: {model_path}")
    
    def predict(self, text: str) -> dict:
        """텍스트에서 엔티티 추출 (후처리 포함)"""
        # 공백 기준으로 토큰화
        words = text.split()
        
        inputs = self.tokenizer(
            words,
            is_split_into_words=True,
            return_tensors='pt',
            truncation=True,
            max_length=128,
        )
        
        with torch.no_grad():
            outputs = self.model(**inputs)
        
        predictions = torch.argmax(outputs.logits, dim=-1)[0]
        word_ids = inputs.word_ids()
        
        # 단어별 라벨 매핑
        entities = []
        current_entity = None
        current_label = None
        prev_word_id = None
        
        for idx, word_id in enumerate(word_ids):
            if word_id is None:
                continue
            
            label_id = predictions[idx].item()
            label = self.id2label[label_id]
            
            # 새 단어 시작
            if word_id != prev_word_id:
                if label.startswith('B-'):
                    # 이전 엔티티 저장
                    if current_entity:
                        entities.append({
                            'text': current_entity,
                            'type': current_label,
                        })
                    # 새 엔티티 시작
                    current_entity = words[word_id]
                    current_label = label[2:]  # B-STORE → STORE
                elif label == 'O':
                    if current_entity:
                        entities.append({
                            'text': current_entity,
                            'type': current_label,
                        })
                    current_entity = None
                    current_label = None
            else:
                # 같은 단어의 후속 토큰 (I- 라벨)
                if current_entity and label.startswith('I-'):
                    pass  # 같은 단어 내므로 무시
            
            prev_word_id = word_id
        
        # 마지막 엔티티 저장
        if current_entity:
            entities.append({
                'text': current_entity,
                'type': current_label,
            })
        
        # 타입별로 분류
        stores = [e['text'] for e in entities if e['type'] == 'STORE']
        menus = [e['text'] for e in entities if e['type'] == 'MENU']
        
        # ✅ 후처리 적용
        stores = self.postprocessor.process(stores, entity_type='store')
        menus = self.postprocessor.process(menus, entity_type='menu')
        
        return {
            'stores': stores,
            'menus': menus,
            'all_entities': entities,
        }


def main():
    print("=" * 60)
    print("🔍 NER 실전 테스트 (Phase 4 개선 버전)")
    print("=" * 60)
    
    predictor = MenuNERPredictor()
    
    # ─────────────── 테스트 케이스 ───────────────
    test_cases = [
        # 1. 합성 데이터 스타일 (학습한 패턴)
        "올래국수에서 고기국수를 먹었어요",
        "우진해장국의 몸국이 진짜 맛있어요",
        
        # 2. 실제 블로그 스타일 (실전)
        "제주 여행 첫 끼로 올래국수 갔는데 고기국수 진짜 맛있었어요",
        "셰프와흑돼지에서 흑돼지구이 먹고 왔는데 존맛...",
        "자매국수 고기국수 곱빼기 시켰는데 양 많음",
        "돈사돈 근고기 2인분 + 된장찌개 주문",
        
        # 3. 복잡한 문장 (어려운 케이스)
        "올래국수 말고 자매국수도 가봤는데 고기국수는 올래가 더 나음",
        "제주 흑돼지 맛집 셰프와흑돼지, 근고기랑 흑돼지구이 둘 다 맛있음",
        "웨이팅 30분했지만 우진해장국 몸국 먹을 가치 있음",
        
        # 4. 메뉴 없는 문장 (부정 케이스)
        "제주 여행 다녀왔어요",
        "올래국수 분위기가 좋아요",
    ]
    
    print("\n📋 테스트 결과:")
    print("-" * 60)
    
    for i, text in enumerate(test_cases, 1):
        result = predictor.predict(text)
        print(f"\n[{i}] 입력: {text}")
        print(f"    가게: {result['stores'] or '없음'}")
        print(f"    메뉴: {result['menus'] or '없음'}")
    
    print("\n" + "=" * 60)
    print("💡 개선 후 성능 평가:")
    print("   - 조사 제거: '올래국수에서' → '올래국수'")
    print("   - 지역명 필터링: '제주' 메뉴에서 제거")
    print("   - 슬랭 필터링: '존맛...' 제거")
    print("   - 부정 케이스: 메뉴 없음 확인")


if __name__ == '__main__':
    main()