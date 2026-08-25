"""
NLP: NER 모델 추론기 (KLUE-RoBERTa)
=====================================
- 학습된 모델로 실제 텍스트에서 엔티티 추출
- 후처리기 연동 (store_name 전달)
- GPU 자동 감지

수정 사항:
- predict() 반환 타입을 TypedDict로 명시
- all_entities 타입 호환성 해결
"""

from __future__ import annotations

import json
import os
from typing import TypedDict

import torch
from transformers import AutoModelForTokenClassification, AutoTokenizer

from ...observability.logging import get_logger
from ...settings import get_settings
from .postprocessor import NERPostprocessor

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════
# 반환 타입 정의 (TypedDict)
# ═══════════════════════════════════════════════════════════


class EntityDict(TypedDict):
    """개체 딕셔너리"""

    text: str
    type: str


class PredictResult(TypedDict):
    """predict() 반환 타입"""

    stores: list[str]
    menus: list[str]
    all_entities: list[EntityDict]


class MenuNERPredictor:
    """
    NER 모델 추론기

    안정성 개선:
    - 모델 로드 실패 시 graceful degradation
    - _model_available 플래그로 폴백 처리
    """

    def __init__(self, model_path: str | None = None):
        self.settings = get_settings()

        if model_path is None:
            model_path = self.settings.ner_model_dir

        # 모델 가용성 플래그 (안정성 개선)
        self._model_available = False

        if not os.path.exists(model_path):
            logger.warning(
                'ner_model_path_not_found',
                model_path=model_path,
            )
            self.postprocessor = NERPostprocessor()
            return

        # 디바이스 자동 감지 (GPU/CPU)
        self.device = self._detect_device()

        try:
            self.tokenizer = AutoTokenizer.from_pretrained(model_path)
            self.model = AutoModelForTokenClassification.from_pretrained(model_path)
            self.model.to(self.device)
            self.model.eval()

            # 라벨 정보 로드
            label_info_path = os.path.join(model_path, 'label_info.json')
            with open(label_info_path, encoding='utf-8') as f:
                label_info = json.load(f)
            self.label2id = label_info['label2id']
            self.id2label = {int(k): v for k, v in label_info['id2label'].items()}

            self._model_available = True

            logger.info(
                'ner_model_loaded',
                model_path=model_path,
                device=str(self.device),
                labels=list(self.id2label.values()),
            )

        except Exception as e:
            logger.error(
                'ner_model_load_failed',
                model_path=model_path,
                error=str(e),
                error_type=type(e).__name__,
            )
            self._model_available = False

        # 후처리기 초기화
        self.postprocessor = NERPostprocessor()

        logger.info(
            'ner_model_loaded',
            model_path=model_path,
            device=str(self.device),
            labels=list(self.id2label.values()),
        )

    def _detect_device(self) -> torch.device:
        """디바이스 자동 감지 (GPU/CPU)"""
        if torch.cuda.is_available():
            device = torch.device('cuda')
            logger.info('ner_using_gpu', gpu_name=torch.cuda.get_device_name(0))
        else:
            device = torch.device('cpu')
            logger.info('ner_using_cpu')
        return device

    def predict(self, text: str, store_name: str = '') -> PredictResult:
        """
        텍스트에서 엔티티 추출

        안정성 개선:
        - 모델 로드 실패 시 빈 결과 반환 (graceful degradation)
        """
        # 모델 가용성 확인 (안정성 개선)
        if not self._model_available:
            logger.warning('ner_model_unavailable_returning_empty')
            return {'stores': [], 'menus': [], 'all_entities': []}

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

        # 디바이스로 이동
        inputs = {k: v.to(self.device) for k, v in inputs.items()}

        # 추론
        with torch.no_grad():
            outputs = self.model(**inputs)
            predictions = torch.argmax(outputs.logits, dim=-1)[0].tolist()

        # 엔티티 추출
        tokens = self.tokenizer.convert_ids_to_tokens(inputs['input_ids'][0])
        word_ids = inputs.word_ids(0)

        entities: list[EntityDict] = []
        current_entity = ''
        current_label: str | None = None
        prev_word_id: int | None = None

        for token, label_id, word_id in zip(tokens, predictions, word_ids, strict=False):
            if word_id is None:
                continue

            label = self.id2label[label_id]

            if label.startswith('B-'):
                # 새 엔티티 시작
                if current_entity:
                    entities.append(
                        {
                            'text': current_entity,
                            'type': current_label or 'UNKNOWN',
                        }
                    )
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
                    entities.append(
                        {
                            'text': current_entity,
                            'type': current_label or 'UNKNOWN',
                        }
                    )
                current_entity = ''
                current_label = None

            prev_word_id = word_id

        # 마지막 엔티티 저장
        if current_entity:
            entities.append(
                {
                    'text': current_entity,
                    'type': current_label or 'UNKNOWN',
                }
            )

        # 타입별 분류
        stores = [e['text'] for e in entities if e['type'] == 'STORE']
        menus = [e['text'] for e in entities if e['type'] == 'MENU']

        # 후처리 적용 (store_name 전달)
        stores = self.postprocessor.process(stores, entity_type='store', store_name=store_name)
        menus = self.postprocessor.process(menus, entity_type='menu', store_name=store_name)

        return {
            'stores': stores,
            'menus': menus,
            'all_entities': entities,
        }
