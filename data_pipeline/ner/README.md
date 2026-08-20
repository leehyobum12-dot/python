# NER 메뉴 추출 모듈

## 개요
공식 API 텍스트에서 가게명/메뉴명을 추출하는 NER 파이프라인.
하이브리드 방식(NER + 로컬 LLM)으로 정확도와 속도 균형 확보.

## 폴더 구조 및 역할

| 폴더 | 역할 | 자동화 사용 | 상태 |
|------|------|-------------|------|
| `core/` | 하이브리드 파이프라인 핵심 | ✅ | 활성 |
| `preprocessing/` | DB 통합, 학습 데이터 생성 | ⚠️ 일부 | 활성 |
| `inference/` | NER 추론, 후처리 | ✅ | 활성 |
| `training/` | 모델 학습/재학습 | ❌ | 보류 |

## 자동화 파이프라인 흐름
official_enricher.py
↓
core/ner_pipeline.py (NER 추출)
↓
inference/ner_predictor.py (모델 추론)
↓
inference/ner_postprocessor.py (후처리)
↓
[신뢰도 낮을 시] core/llm_verifier.py (로컬 LLM 검증)
↓
최종 메뉴 반환

## 임포트 경로 규칙
- 패키지 내부: 상대 임포트 (`from ..inference.ner_predictor import ...`)
- 외부 모듈: 절대 임포트 (`from enrich.ner.core.ner_pipeline import ...`)

## 보류 항목 (재학습)
- `preprocessing/archive/`: LLM 라벨링, BIO 태깅, 검증 스크립트
- `training/archive/`: 재학습 매니저
- 재학습 진행 시 archive에서 꺼내서 사용

## 실행 방법
```bash
# 하이브리드 추출기 테스트
cd data_pipeline/enrich
python -m ner.core.hybrid_extractor

# 후처리기 테스트
python -m ner.inference.ner_postprocessor

---

## 🔧 Step 3: 임포트 경로 최종 확인

자동화 시 사용되는 임포트 경로를 명확히 정리:

### `official_enricher.py` (외부 모듈)
```python
# 절대 임포트 사용
try:
    from .ner.core.ner_pipeline import NERMenuPipeline
except ImportError:
    from ner.core.ner_pipeline import NERMenuPipeline

# 폴더구조
data_pipeline/
├── __init__.py
│
├── config/                          ← [신설] SSOT 중앙 관리
│   ├── __init__.py
│   ├── constants.py                 ← 화이트리스트/블랙리스트/조리법/재료 (유일한 진실)
│   ├── thresholds.py                ← LLM_VERIFY_THRESHOLD, CONFIDENCE_WEIGHTS
│   └── paths.py                     ← 모델/데이터/로그 경로 상수
│
├── adapters/                        ← [유지] 공공데이터 수집만 담당
│   ├── __init__.py
│   ├── base.py
│   ├── food_safety.py
│   ├── low_sodium.py
│   ├── model_restaurant.py
│   └── safe_restaurant.py
│
├── enrich/                          ← [경량화] 정제/분류만 담당
│   ├── __init__.py
│   ├── classifier.py                ← taxonomy.py 흡수 (7차원 태그 분류)
│   ├── geocoder.py
│   ├── naver_places.py
│   └── official_enricher.py         ← 오케스트레이터 (API 호출만, NER에 위임)
│
├── ner/                             ← [승격] enrich/ner → data_pipeline/ner (최상위)
│   ├── __init__.py
│   ├── core/
│   │   ├── __init__.py
│   │   ├── hybrid_extractor.py      ← [수정] 신뢰도 계산 로직 교체
│   │   ├── llm_verifier.py          ← [수정] Ollama 연동 강화
│   │   └── ner_pipeline.py          ← [수정] config 경로 참조
│   ├── inference/
│   │   ├── __init__.py
│   │   ├── ner_postprocessor.py     ← [수정] 상수 → config.constants 참조
│   │   └── ner_predictor.py         ← [수정] 모델 경로 → config.paths 참조
│   ├── preprocessing/
│   │   ├── __init__.py
│   │   ├── food_db_integrator.py    ← [수정] 상수 → config.constants 참조
│   │   └── ner_data_generator.py
│   ├── training/
│   │   ├── __init__.py
│   │   └── ner_trainer.py
│   ├── data/                        ← 데이터 폴더
│   │   ├── source/
│   │   ├── processed/
│   │   ├── training/
│   │   └── real_data/
│   └── models/                      ← 모델 저장
│       └── ner_model/
│
├── storage/                         ← [유지] 로컬 캐시
├── out/                             ← [유지] 출력 파일
│
├── pipeline.py                      ← [유지] 메인 파이프라인
├── ingest_to_supabase.py            ← [유지] Supabase 적재
└── test_jeju.py                     ← [유지] 제주도 검증 스크립트

flows/                               ← [유지] Prefect 오케스트레이션
├── menu_enrich_flow.py
└── national_pipeline.py