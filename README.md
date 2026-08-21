# 바른인증식당 데이터 파이프라인

공공데이터 수집/정제/적재 자동화 파이프라인입니다.

## 아키텍처
[Adapters] → [Bronze] → [Silver] → [Gold] → [Supabase]
수집 원본 보존 정제/병합 DB 매칭 적재

## 설치

```bash
# 1. 저장소 클론
git clone <repo-url>
cd bareun/app/python

# 2. 가상환경 생성 (uv 권장)
uv venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 3. 의존성 설치
uv pip install -e ".[dev]"

# 4. 환경변수 설정
cp .env.example .env
# .env 파일에 실제 API 키 입력

사용법
# 전국 수집
bareun-pipeline bronze-collect

# 단일 시도 수집
bareun-pipeline bronze-collect --province 제주특별자치도

# 강제 수집 (체크포인트 무시)
bareun-pipeline bronze-collect --province 제주특별자치도 --force

# 쿼터 현황
bareun-pipeline quota-status

# 체크포인트 초기화
bareun-pipeline reset-checkpoint --province 제주특별자치도

Prefect 워크플로우
# Prefect Server 실행 (별도 터미널)
prefect server start

# 전국 파이프라인 데몬 (매일 KST 04:00)
python flows/national_pipeline.py

# 메뉴 enrichment 데몬 (매일 KST 01:00)
python flows/menu_enrich_flow.py

폴더 구조
src/bareun_pipeline/
├── adapters/          # 원본 수집 (4개 공공데이터 API)
├── bronze/            # 원본 보존 (NDJSON + Supabase)
├── silver/            # 정제/병합/보강/분류
├── gold/              # DB 매칭/출력/적재
├── domain/            # 도메인 모델/비즈니스 규칙
├── nlp/               # NER + LLM 메뉴 추출
├── training/          # NER 모델 학습
├── preprocessing/     # 음식 DB 전처리
├── io/                # 입출력/체크포인트
├── observability/     # 로깅/쿼터 관리
└── cli.py             # CLI 엔트리포인트

의존성
Python 3.11+
Supabase (PostgreSQL + PostGIS)
Prefect 3.x
pyahocorasick (Aho-Corasick 문자열 매칭)
transformers + torch (KLUE-RoBERTa NER)
Ollama (로컬 LLM, 선택)

보안
.env 파일은 절대 Git에 커밋하지 마세요
SUPABASE_SERVICE_ROLE_KEY는 로컬 파이프라인 전용입니다
PII 필드는 원본 저장 전 자동 마스킹됩니다
전화번호는 salted SHA-256으로 해시화됩니다

---

### [Quality Gate & Phase Decision]

#### 자가 감사 결과

| 항목 | 상태 | 근거 |
|---|---|---|
| SQL 마이그레이션 | ✅ 완료 | 001, 002 마이그레이션 파일 작성 |
| README.md | ✅ 완료 | 설치/사용법 문서화 |
| pyahocorasick 패키지명 | ✅ 확인 | `pyproject.toml`에 `pyahocorasick>=2.0.0` |

#### Phase 2 완료 요약

**구현한 파일:**
- `sql/migrations/001_initial_schema.sql` - 초기 스키마 (v4.8)
- `sql/migrations/002_certs_to_array.sql` - certs text → text[]
- `README.md` - 설치/사용법 가이드

---

### [전체 프로젝트 완료 요약]

**Phase 1 (Step 1~16):** 52개 Python 파일 구현 완료
**Phase 2:** 2개 SQL 마이그레이션 + README.md 완료

**총 구현 파일:** 55개

| 계층 | 파일 수 | 핵심 기능 |
|---|---|---|
| 설정 | 4 | pyproject.toml, .gitignore, pyrightconfig.json, .env.example |
| 도메인 | 7 | CertRecord, dedup, privacy, taxonomy, constants, avoid_tags |
| Adapters | 6 | BaseAdapter + 4개 공공데이터 어댑터 |
| Bronze | 3 | 수집 orchestration, 원본 보존 |
| Silver | 5 | 정규화, 병합, 분류, 보강, 메뉴추출 |
| Gold | 4 | DB 매칭, 출력, Supabase 적재 |
| NLP | 8 | NER 예측, 후처리, 파이프라인, LLM, 하이브리드 |
| Training | 6 | 데이터 생성, 라벨링, 검증, 혼합, 학습 |
| IO/Observability | 5 | 체크포인트, 로깅, 쿼터 |
| CLI/Flows | 3 | CLI, Prefect 워크플로우 |
| Preprocessing | 2 | 음식 DB 통합 |
| Scripts | 2 | CSV 검수, 단일 테스트 |
| SQL | 2 | 마이그레이션 |
| 문서 | 1 | README.md |

---

**프로젝트가 완료되었습니다. 추가 수정이나 보완이 필요한 부분이 있으시면 말씀해 주세요.**