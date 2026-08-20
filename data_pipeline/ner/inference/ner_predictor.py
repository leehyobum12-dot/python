# data_pipeline/ner/inference/ner_predictor.py
"""
NER 모델 추론 스크립트 v6
========================
- 학습된 모델로 실제 텍스트에서 엔티티 추출
- 후처리기 v6 연동 (store_name 전달)
"""
import os
import json
import torch
from transformers import AutoModelForTokenClassification, AutoTokenizer

try:
    from .ner_postprocessor import NERPostprocessor
    from ...config.paths import NER_MODEL_DIR
except ImportError:
    from data_pipeline.ner.inference.ner_postprocessor import NERPostprocessor
    from data_pipeline.config.paths import NER_MODEL_DIR


class MenuNERPredictor:
    """NER 모델 추론기"""

    def __init__(self, model_path: str = None):
        if model_path is None:
            model_path = NER_MODEL_DIR

        if not os.path.exists(model_path):
            raise FileNotFoundError(f"모델 경로 없음: {model_path}")

        self.tokenizer = AutoTokenizer.from_pretrained(model_path)
        self.model = AutoModelForTokenClassification.from_pretrained(model_path)
        self.model.eval()

        # 라벨 정보 로드
        label_info_path = os.path.join(model_path, 'label_info.json')
        with open(label_info_path, 'r', encoding='utf-8') as f:
            label_info = json.load(f)
        self.label2id = label_info['label2id']
        self.id2label = {int(k): v for k, v in label_info['id2label'].items()}

        # 후처리기 초기화
        self.postprocessor = NERPostprocessor()

        print(f"✅ NER 모델 로드 완료: {model_path}")

    def predict(self, text: str, store_name: str = '') -> dict:
        """텍스트에서 엔티티 추출"""
        if not text or not text.strip():
            return {'stores': [], 'menus': [], 'all_entities': []}

        # 토큰화
        inputs = self.tokenizer(
            text,
            return_tensors='pt',
            truncation=True,
            max_length=512,
            padding=True,
        )

        # 추론
        with torch.no_grad():
            outputs = self.model(**inputs)
            predictions = torch.argmax(outputs.logits, dim=-1)[0].tolist()

        # 엔티티 추출
        tokens = self.tokenizer.convert_ids_to_tokens(
            inputs['input_ids'][0]
        )
        word_ids = inputs.word_ids(0)

        entities = []
        current_entity = ''
        current_label = None
        prev_word_id = None

        for token, label_id, word_id in zip(
            tokens, predictions, word_ids
        ):
            if word_id is None or token in ('[CLS]', '[SEP]', '[PAD]'):
                continue

            label = self.id2label[label_id]

            if label.startswith('B-'):
                # 새 엔티티 시작
                if current_entity:
                    entities.append({
                        'text': current_entity,
                        'type': current_label,
                    })
                current_entity = token.replace('##', '')
                current_label = label[2:]
            elif label.startswith('I-') and current_label == label[2:]:
                # 같은 엔티티 계속
                if word_id != prev_word_id:
                    current_entity += ' '
                current_entity += token.replace('##', '')
            else:
                # 엔티티 종료
                if current_entity:
                    entities.append({
                        'text': current_entity,
                        'type': current_label,
                    })
                current_entity = ''
                current_label = None

            prev_word_id = word_id

        # 마지막 엔티티 저장
        if current_entity:
            entities.append({
                'text': current_entity,
                'type': current_label,
            })

        # 타입별 분류
        stores = [e['text'] for e in entities if e['type'] == 'STORE']
        menus = [e['text'] for e in entities if e['type'] == 'MENU']

        # 후처리 적용 (store_name 전달)
        stores = self.postprocessor.process(
            stores, entity_type='store', store_name=store_name
        )
        menus = self.postprocessor.process(
            menus, entity_type='menu', store_name=store_name
        )

        return {
            'stores': stores,
            'menus': menus,
            'all_entities': entities,
        }


# ─────────────── 단독 실행 (테스트) ───────────────

def main():
    print("=" * 60)
    print("🔍 NER 실전 테스트 (Phase 4 개선 버전)")
    print("=" * 60)

    predictor = MenuNERPredictor()

    test_cases = [
        "올래국수에서 고기국수를 먹었어요",
        "우진해장국의 몸국이 진짜 맛있어요",
        "제주 여행 첫 끼로 올래국수 갔는데 고기국수 진짜 맛있었어요",
        "셰프와흑돼지에서 흑돼지구이 먹고 왔는데 존맛...",
        "자매국수 고기국수 곱빼기 시켰는데 양 많음",
        "돈사돈 근고기 2인분 + 된장찌개 주문",
        "올래국수 말고 자매국수도 가봤는데 고기국수는 올래가 더 나음",
        "제주 흑돼지 맛집 셰프와흑돼지, 근고기랑 흑돼지구이 둘 다 맛있음",
        "웨이팅 30분했지만 우진해장국 몸국 먹을 가치 있음",
        "제주 여행 다녀왔어요",
        "올래국수 분위기가 좋아요",
    ]

    for i, text in enumerate(test_cases, 1):
        result = predictor.predict(text)
        print(f"\n[{i}] 입력: {text}")
        print(f"  가게: {result['stores'] or '없음'}")
        print(f"  메뉴: {result['menus'] or '없음'}")

    print("\n" + "=" * 60)


if __name__ == '__main__':
    main()