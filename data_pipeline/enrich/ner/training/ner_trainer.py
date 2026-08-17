# data_pipeline/enrich/ner/training/ner_trainer.py
"""
NER 모델 학습 스크립트 (Phase 3) - 최신 transformers API 대응
=================================================
수정 사항:
- ✅ evaluation_strategy → eval_strategy
- ✅ tokenizer → processing_class
- ✅ trainer.evaluate() 인자 제거 (타입 에러 해결)
"""
import os
import json
import numpy as np
from typing import List, Dict, Optional

from datasets import Dataset, DatasetDict
from transformers import (
    AutoModelForTokenClassification,
    AutoTokenizer,
    TrainingArguments,
    Trainer,
    DataCollatorForTokenClassification,
    EarlyStoppingCallback,
)
from seqeval.metrics import (
    precision_score,
    recall_score,
    f1_score,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
MODEL_DIR = os.path.join(BASE_DIR, 'models')

# 데이터 경로
TRAIN_PATH = os.path.join(DATA_DIR, 'ner_train.json')
VAL_PATH = os.path.join(DATA_DIR, 'ner_val.json')
TEST_PATH = os.path.join(DATA_DIR, 'ner_test.json')

# 모델 저장 경로
MODEL_SAVE_PATH = os.path.join(MODEL_DIR, 'ner_model')

# ─────────────── 라벨 정의 ───────────────
LABEL_LIST = ['O', 'B-STORE', 'I-STORE', 'B-MENU', 'I-MENU']
label2id = {label: i for i, label in enumerate(LABEL_LIST)}
id2label = {i: label for i, label in enumerate(LABEL_LIST)}

# ─────────────── 모델 설정 ───────────────
MODEL_NAME = 'beomi/kcbert-base'  # 한국어 BERT

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


# ═══════════════════════════════════════════════════════════
# 데이터 로드
# ═══════════════════════════════════════════════════════════
def load_json_data(path: str) -> Dataset:
    """JSON 파일 로드 및 Dataset 변환"""
    if not os.path.exists(path):
        raise FileNotFoundError(f"데이터 파일 없음: {path}")
    
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    print(f"📄 데이터 로드: {os.path.basename(path)} ({len(data):,}건)")
    return Dataset.from_list(data)


def prepare_datasets() -> DatasetDict:
    """train/val/test 데이터셋 준비"""
    train_data = load_json_data(TRAIN_PATH)
    val_data = load_json_data(VAL_PATH)
    test_data = load_json_data(TEST_PATH)
    
    return DatasetDict({
        'train': train_data,
        'val': val_data,
        'test': test_data,
    })


# ═══════════════════════════════════════════════════════════
# 토큰화 및 라벨 정렬
# ═══════════════════════════════════════════════════════════
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


# ═══════════════════════════════════════════════════════════
# 평가 메트릭
# ═══════════════════════════════════════════════════════════
def compute_metrics(eval_pred):
    """NER 평가 메트릭 계산"""
    predictions, labels = eval_pred
    predictions = np.argmax(predictions, axis=-1)
    
    # -100 제거 (특수 토큰 무시)
    true_predictions = []
    true_labels = []
    
    for pred, label in zip(predictions, labels):
        pred_seq = []
        label_seq = []
        for p, l in zip(pred, label):
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


# ═══════════════════════════════════════════════════════════
# 메인 학습
# ═══════════════════════════════════════════════════════════
def main():
    print("=" * 60)
    print("🎯 NER 모델 학습 (Phase 3)")
    print("=" * 60)
    print(f"모델: {MODEL_NAME}")
    print(f"라벨: {LABEL_LIST}")
    print(f"설정: 배치={TRAINING_CONFIG['per_device_train_batch_size']}, "
          f"에폭={TRAINING_CONFIG['num_train_epochs']}, "
          f"fp16={TRAINING_CONFIG['fp16']}")
    print()
    
    # ───────────────────────────────────────────────────────
    # Step 1: 토크나이저 및 모델 로드
    # ───────────────────────────────────────────────────────
    print("📥 토크나이저 로드 중...")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    
    print("📥 모델 로드 중...")
    model = AutoModelForTokenClassification.from_pretrained(
        MODEL_NAME,
        num_labels=len(LABEL_LIST),
        id2label=id2label,
        label2id=label2id,
    )
    print(f"✅ 모델 파라미터: {sum(p.numel() for p in model.parameters()):,}개")
    
    # ───────────────────────────────────────────────────────
    # Step 2: 데이터셋 준비 및 토큰화
    # ───────────────────────────────────────────────────────
    print("\n📊 데이터셋 준비 중...")
    datasets = prepare_datasets()
    
    print("🔧 토큰화 중...")
    max_length = TRAINING_CONFIG['max_seq_length']
    tokenized_datasets = datasets.map(
        lambda x: tokenize_and_align_labels(x, tokenizer, max_length),
        batched=True,
        remove_columns=datasets['train'].column_names,
        desc="Tokenizing",
    )
    
    # 데이터 콜레이터 (동적 패딩)
    data_collator = DataCollatorForTokenClassification(tokenizer=tokenizer)
    
    # ───────────────────────────────────────────────────────
    # Step 3: 학습 설정
    # ───────────────────────────────────────────────────────
    print("\n⚙️ 학습 설정 중...")
    training_args = TrainingArguments(
        output_dir=MODEL_SAVE_PATH,
        # 학습 설정
        per_device_train_batch_size=TRAINING_CONFIG['per_device_train_batch_size'],
        per_device_eval_batch_size=TRAINING_CONFIG['per_device_eval_batch_size'],
        num_train_epochs=TRAINING_CONFIG['num_train_epochs'],
        learning_rate=TRAINING_CONFIG['learning_rate'],
        weight_decay=TRAINING_CONFIG['weight_decay'],
        warmup_ratio=TRAINING_CONFIG['warmup_ratio'],
        # 평가 설정
        # ✅ 수정: evaluation_strategy → eval_strategy (최신 API)
        eval_strategy='epoch',
        save_strategy='epoch',
        load_best_model_at_end=True,
        metric_for_best_model='f1',
        greater_is_better=True,
        # Mixed Precision (VRAM 절약)
        fp16=TRAINING_CONFIG['fp16'],
        # 로그 설정
        logging_steps=100,
        logging_dir=os.path.join(MODEL_SAVE_PATH, 'logs'),
        report_to='none',  # wandb 등 비활성화
        # 성능 최적화
        dataloader_num_workers=2,
        seed=42,
    )
    
    # ───────────────────────────────────────────────────────
    # Step 4: Trainer 생성 및 학습
    # ───────────────────────────────────────────────────────
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=tokenized_datasets['train'],
        eval_dataset=tokenized_datasets['val'],
        # ✅ 수정: tokenizer → processing_class (최신 API)
        processing_class=tokenizer,
        data_collator=data_collator,
        compute_metrics=compute_metrics,
        callbacks=[
            EarlyStoppingCallback(early_stopping_patience=2),
        ],
    )
    
    print("\n🚀 학습 시작!")
    print("-" * 60)
    train_result = trainer.train()
    print("-" * 60)
    
    # 학습 결과 출력
    print(f"\n📈 학습 완료:")
    print(f"   Training Loss: {train_result.training_loss:.4f}")
    print(f"   학습 시간: {train_result.metrics['train_runtime']:.1f}초")
    
    # ───────────────────────────────────────────────────────
    # Step 5: 검증 세트 평가
    # ───────────────────────────────────────────────────────
    print("\n📊 검증 세트 평가:")
    # ✅ 수정: 인자 없이 호출 (eval_dataset 이미 설정됨)
    val_metrics = trainer.evaluate()
    print(f"   Precision: {val_metrics['eval_precision']:.4f}")
    print(f"   Recall: {val_metrics['eval_recall']:.4f}")
    print(f"   F1: {val_metrics['eval_f1']:.4f}")
    
    # ───────────────────────────────────────────────────────
    # Step 6: 테스트 세트 최종 평가
    # ───────────────────────────────────────────────────────
    print("\n📊 테스트 세트 최종 평가:")
    # ✅ 수정: eval_dataset을 test로 변경 후 인자 없이 호출
    trainer.eval_dataset = tokenized_datasets['test']
    test_metrics = trainer.evaluate()
    print(f"   Precision: {test_metrics['eval_precision']:.4f}")
    print(f"   Recall: {test_metrics['eval_recall']:.4f}")
    print(f"   F1: {test_metrics['eval_f1']:.4f}")
    
    # ───────────────────────────────────────────────────────
    # Step 7: 모델 저장
    # ───────────────────────────────────────────────────────
    print("\n💾 모델 저장 중...")
    os.makedirs(MODEL_SAVE_PATH, exist_ok=True)
    trainer.save_model(MODEL_SAVE_PATH)
    tokenizer.save_pretrained(MODEL_SAVE_PATH)
    
    # 라벨 정보 저장 (추론 시 사용)
    label_info = {
        'labels': LABEL_LIST,
        'label2id': label2id,
        'id2label': id2label,
        'model_name': MODEL_NAME,
    }
    with open(os.path.join(MODEL_SAVE_PATH, 'label_info.json'), 'w', encoding='utf-8') as f:
        json.dump(label_info, f, ensure_ascii=False, indent=2)
    
    print(f"✅ 모델 저장 완료: {MODEL_SAVE_PATH}")
    
    # ───────────────────────────────────────────────────────
    # 최종 요약
    # ───────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("🎉 NER 모델 학습 완료!")
    print("=" * 60)
    print(f"   검증 F1: {val_metrics['eval_f1']:.4f}")
    print(f"   테스트 F1: {test_metrics['eval_f1']:.4f}")
    print(f"   모델 경로: {MODEL_SAVE_PATH}")
    print(f"   다음 단계: NER 추론 테스트 (ner_predictor.py)")


if __name__ == '__main__':
    main()