# data_pipeline/config/paths.py
"""
[SSOT] 파일 시스템 경로 상수
============================
절대 경로를 하드코딩하지 마세요. 이 파일에서 관리합니다.
"""
import os

# data_pipeline 루트
PIPELINE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# NER 도메인
NER_ROOT = os.path.join(PIPELINE_ROOT, 'ner')
NER_MODEL_DIR = os.path.join(NER_ROOT, 'models', 'ner_model')
NER_DATA_DIR = os.path.join(NER_ROOT, 'data')

# 실전 데이터 로깅
REAL_DATA_LOG_PATH = os.path.join(NER_DATA_DIR, 'real_data_log.jsonl')

# 학습 데이터
FOOD_NAMES_PATH = os.path.join(NER_DATA_DIR, 'food_names.txt')
NER_TRAIN_PATH = os.path.join(NER_DATA_DIR, 'ner_train.json')
NER_VAL_PATH = os.path.join(NER_DATA_DIR, 'ner_val.json')
NER_TEST_PATH = os.path.join(NER_DATA_DIR, 'ner_test.json')

# 출력
OUTPUT_DIR = os.path.join(PIPELINE_ROOT, 'out')
REPORT_DIR = os.path.join(OUTPUT_DIR, 'reports')

# Ollama
OLLAMA_API_URL = "http://localhost:11434/api/generate"
OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"
OLLAMA_MODEL = "qwen2.5:7b"