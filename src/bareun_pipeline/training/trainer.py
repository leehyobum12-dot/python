"""
NER 모델 학습 (KLUE-RoBERTa)
===============================
- transformers Trainer API 사용
- Early Stopping
- Mixed Precision (fp16)

기존 참고:
- ner/training/ner_trainer.py 전체 구조
- KLUE-RoBERTa 학습 설정
- EarlyStoppingCallback

보존 사항:
- KLUE-RoBERTa 학습
- Early Stopping
- Mixed Precision

개선 사항:
- 구조화 로깅
- 타입 안전성 강화
"""

from __future__ import annotations

import json
import os

import numpy as np
from datasets import Dataset, DatasetDict
from seqeval.metrics import (
    f1_score,
    precision_score,
    recall_score,
)
from transformers import (
    AutoModelForTokenClassification,
    AutoTokenizer,
    DataCollatorForTokenClassification,
    EarlyStoppingCallback,
    Trainer,
    TrainingArguments,
)

from ..observability.logging import get_logger

logger = get_logger(__name__)


# ─────────────── 라벨 정의 ───────────────
LABEL_LIST = ['O', 'B-STORE', 'I-STORE', 'B-MENU', 'I-MENU']
label2id = {label: i for i, label in enumerate(LABEL_LIST)}
id2label = {i: label for i, label in enumerate(LABEL_LIST)}

# 모델 설정
MODEL_NAME = 'klue/roberta-base'

# 학습 하이퍼파라미터 (RTX 4060 최적화)
TRAINING_CONFIG = {
    'per_device_train_batch_size': 16,
    'per_device_eval_batch_size': 16,
    'num_train_epochs': 3,
    'learning_rate': 5e-5,
    'weight_decay': 0.01,
    'warmup_ratio': 0.1,
    'max_seq_length': 128,
    'fp16': True,  # Mixed Precision (VRAM 절약)
}


def tokenize_and_align_labels(examples, tokenizer, max_length):
    """토큰화 + BIO 라벨 정렬"""
    tokenized = tokenizer(
        examples['tokens'],
        truncation=True,
        is_split_into_words=True,
        max_length=max_length,
        padding=False,
    )

    all_labels = []
    for i, labels in enumerate(examples['labels']):
        word_ids = tokenized.word_ids(batch_index=i)
        label_ids = []
        previous_word_idx = None

        for word_idx in word_ids:
            if word_idx is None:
                # 특수 토큰 ([CLS], [SEP]) → 무시
                label_ids.append(-100)
            elif word_idx != previous_word_idx:
                # 단어의 첫 토큰
                label_id = label2id[labels[word_idx]]
                label_ids.append(label_id)
            else:
                # 단어의 후속 토큰
                label_id = label2id[labels[word_idx]]
                if label_id == 0:  # O 라벨
                    label_ids.append(0)
                elif label_id in (1, 3):  # B-STORE → I-STORE, B-MENU → I-MENU
                    label_ids.append(label_id + 1)
                else:  # I-STORE, I-MENU 유지
                    label_ids.append(label_id)
            previous_word_idx = word_idx

        all_labels.append(label_ids)

    tokenized['labels'] = all_labels
    return tokenized


def compute_metrics(eval_pred):
    """NER 평가 메트릭 계산"""
    predictions, labels = eval_pred
    predictions = np.argmax(predictions, axis=-1)

    # -100 제거 (특수 토큰 무시)
    true_predictions = []
    true_labels = []

    for pred, label in zip(predictions, labels, strict=False):
        pred_seq = []
        label_seq = []
        for p, l in zip(pred, label, strict=False):
            if l != -100:
                pred_seq.append(id2label[p])
                label_seq.append(id2label[l])
        true_predictions.append(pred_seq)
        true_labels.append(label_seq)

    precision = precision_score(true_labels, true_predictions)
    recall = recall_score(true_labels, true_predictions)
    f1 = f1_score(true_labels, true_predictions)

    return {
        'precision': precision,
        'recall': recall,
        'f1': f1,
    }


def train_ner_model(
    train_data: list[dict],
    val_data: list[dict],
    test_data: list[dict],
    model_save_path: str,
) -> dict[str, float]:
    """
    NER 모델 학습

    기존 ner/training/ner_trainer.py의 main() 함수 보존

    Args:
        train_data: 학습 데이터
        val_data: 검증 데이터
        test_data: 테스트 데이터
        model_save_path: 모델 저장 경로

    Returns:
        학습 메트릭
    """
    logger.info(
        'train_ner_model_start',
        train=len(train_data),
        val=len(val_data),
        test=len(test_data),
        model_save_path=model_save_path,
    )

    # Step 1: 토크나이저 및 모델 로드
    logger.info('loading_tokenizer_and_model')
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)

    model = AutoModelForTokenClassification.from_pretrained(
        MODEL_NAME,
        num_labels=len(LABEL_LIST),
        id2label=id2label,
        label2id=label2id,
    )

    logger.info(
        'model_loaded',
        parameters=sum(p.numel() for p in model.parameters()),
    )

    # Step 2: 데이터셋 준비 및 토큰화
    datasets = DatasetDict(
        {
            'train': Dataset.from_list(train_data),
            'val': Dataset.from_list(val_data),
            'test': Dataset.from_list(test_data),
        }
    )

    max_length = TRAINING_CONFIG['max_seq_length']
    tokenized_datasets = datasets.map(
        lambda x: tokenize_and_align_labels(x, tokenizer, max_length),
        batched=True,
        remove_columns=datasets['train'].column_names,
        desc='Tokenizing',
    )

    data_collator = DataCollatorForTokenClassification(tokenizer=tokenizer)

    # Step 3: 학습 설정
    training_args = TrainingArguments(
        output_dir=model_save_path,
        per_device_train_batch_size=TRAINING_CONFIG['per_device_train_batch_size'],
        per_device_eval_batch_size=TRAINING_CONFIG['per_device_eval_batch_size'],
        num_train_epochs=TRAINING_CONFIG['num_train_epochs'],
        learning_rate=TRAINING_CONFIG['learning_rate'],
        weight_decay=TRAINING_CONFIG['weight_decay'],
        warmup_ratio=TRAINING_CONFIG['warmup_ratio'],
        eval_strategy='epoch',
        save_strategy='epoch',
        load_best_model_at_end=True,
        metric_for_best_model='f1',
        greater_is_better=True,
        fp16=TRAINING_CONFIG['fp16'],
        logging_steps=100,
        logging_dir=os.path.join(model_save_path, 'logs'),
        report_to='none',
        dataloader_num_workers=0,
        seed=42,
    )

    # Step 4: Trainer 생성 및 학습
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=tokenized_datasets['train'],
        eval_dataset=tokenized_datasets['val'],
        processing_class=tokenizer,
        data_collator=data_collator,
        compute_metrics=compute_metrics,
        callbacks=[
            EarlyStoppingCallback(early_stopping_patience=2),
        ],
    )

    logger.info('training_start')
    train_result = trainer.train()

    logger.info(
        'training_complete',
        loss=train_result.training_loss,
        runtime=train_result.metrics.get('train_runtime', 0),
    )

    # Step 5: 검증 세트 평가
    val_metrics = trainer.evaluate()
    logger.info(
        'val_metrics',
        precision=val_metrics.get('eval_precision', 0),
        recall=val_metrics.get('eval_recall', 0),
        f1=val_metrics.get('eval_f1', 0),
    )

    # Step 6: 테스트 세트 최종 평가
    trainer.eval_dataset = tokenized_datasets['test']
    test_metrics = trainer.evaluate()
    logger.info(
        'test_metrics',
        precision=test_metrics.get('eval_precision', 0),
        recall=test_metrics.get('eval_recall', 0),
        f1=test_metrics.get('eval_f1', 0),
    )

    # Step 7: 모델 저장
    os.makedirs(model_save_path, exist_ok=True)
    trainer.save_model(model_save_path)
    tokenizer.save_pretrained(model_save_path)

    # 라벨 정보 저장 (추론 시 사용)
    label_info = {
        'labels': LABEL_LIST,
        'label2id': label2id,
        'id2label': id2label,
        'model_name': MODEL_NAME,
    }
    with open(os.path.join(model_save_path, 'label_info.json'), 'w', encoding='utf-8') as f:
        json.dump(label_info, f, ensure_ascii=False, indent=2)

    logger.info('model_saved', path=model_save_path)

    return {
        'val_f1': val_metrics.get('eval_f1', 0),
        'test_f1': test_metrics.get('eval_f1', 0),
    }
