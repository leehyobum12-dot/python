# 바른인증식당 데이터 파이프라인

공공데이터 수집/정제/적재 자동화 파이프라인입니다.

> **미션**: 공공데이터 간 정보 비대칭 해소, 국민 행복 증진  
> **예산**: 월 10만원 이하 (Free-Tier 극대화, API 과금 차단 필수)  
> **타겟**: 한국 소상공인 및 소비자

## 아키텍처

```
[Adapters] → [Bronze] → [Silver] → [Gold] → [Supabase]
 수집         원본 보존   정제/병합   DB 매칭   적재
```

## 정보 소스 매핑 (확정)

| 정보 | 소스 | 필드 | 신뢰도 |
|------|------|------|--------|
| 사진 (내부/외부) | 관광정보 API | `firstimage`, `firstimage2` | 높음 |
| 메뉴 | 관광정보 API | `treatmenufood` | 높음 |
| 영업시간 | 관광정보 API | `opentimefood` | 높음 |
| 휴무일 | 관광정보 API | `restdatefood` | 높음 |
| 예약 안내 | 관광정보 API | `reservationfood` | 높음 |
| 영업상태 | 인허가 데이터 | `SALS_STTS_NM` | **100% 정확** |
| 위생등급 | 인허가 데이터 | `GRD_SE_NM` (갑/을) | **100% 정확** |

## 매칭 전략

### 멀티 키 스코어 매칭

```
매칭 스코어 = 이름 유사도 × 0.5 + 주소 유사도 × 0.3 + 좌표 근접도 × 0.2
```

| 임계값 | 판단 |
|--------|------|
| 80%+ | ✅ 확실한 매칭 |
| 60~80% | ⚠️ 가능성 있음 (수동 확인 권장) |
| 60% 미만 | ❌ 다른 식당 |

### 좌표 전략

- **네이버 지오코딩 우선**: 주소 통일, 좌표계 변환 오차 대응
- **좌표 오차 유형**:
  - 일반 도심 상가: 5~20m (필지 중심점)
  - 대형 쇼핑몰/복합상가: 50~300m
  - 좌표계 변환 오차 (EPSG:5174 → WGS84): ~300m
- **매칭 반경**: 500m (좌표계 변환 오차 대응)

### 체크포인트 시스템

- **관광공사 일일 트래픽 제한**: 1,000건/일
- **체크포인트 방식**: `content_id` 기반 증분 업데이트
- **매칭 대상**: 관광공사 716건만 (일반음식점 229만 건 매칭 생략)
- **일반음식점**: 영업중/폐업/위생등급 확인만 수행

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
```

## 사용법

```bash
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
```

## Prefect 워크플로우

```bash
# Prefect Server 실행 (별도 터미널)
prefect server start

# 전국 파이프라인 데몬 (매일 KST 04:00)
python flows/national_pipeline.py

# 메뉴 enrichment 데몬 (매일 KST 01:00)
python flows/menu_enrich_flow.py
```

## 폴더 구조

```
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
```

## 의존성

- Python 3.11+
- Supabase (PostgreSQL + PostGIS)
- Prefect 3.x
- pyahocorasick (Aho-Corasick 문자열 매칭)
- transformers + torch (KLUE-RoBERTa NER)
- Ollama (로컬 LLM, 선택)

## 보안

- `.env` 파일은 절대 Git에 커밋하지 마세요
- `SUPABASE_SERVICE_ROLE_KEY`는 로컬 파이프라인 전용입니다
- PII 필드는 원본 저장 전 자동 마스킹됩니다
- 전화번호는 salted SHA-256으로 해시화됩니다

## 구현 파일 현황

| 계층 | 파일 수 | 핵심 기능 |
|------|---------|-----------|
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

**총 구현 파일**: 55개

## 확장 아키텍처 설계

### 1. 메뉴 보강 해결 방안

**문제**: 네이버/카카오 로컬 검색은 메뉴 정보를 제공하지 않음 (확인 완료)

**해결**: 관광정보 `treatmenufood` 필드 활용

```
메뉴 보강 파이프라인:
관광공사 상세 조회 → treatmenufood 추출 → 메뉴 리스트 생성
```

### 2. 사업자등록증 검증 시스템

```
┌─────────────────────────────────────────────────────────────┐
│                    사업자등록증 검증 파이프라인              │
├─────────────────────────────────────────────────────────────┤
│  1. 사업자등록번호 진위확인 (국세청 API)                     │
│     ├── 유효성 검증                                         │
│     ├── 사업주(대표자) 확인                                 │
│     └── 개업일/폐업일 확인                                  │
│                                                              │
│  2. 주소 일치 검증                                          │
│     ├── 사업자등록증 주소 vs 수집된 주소                    │
│     └── 지오코딩으로 좌표 비교 (반경 100m 이내)             │
│                                                              │
│  3. 뱃지(인증) 증명서 검증                                  │
│     ├── 모범음식점: 지정일자, 지정기관 확인                 │
│     ├── 위생등급: 등급(별), 유효기간 확인                   │
│     └── 나트륨줄이기: 지정번호, 유효기간 확인               │
│                                                              │
│  4. 교차 검증 (Cross-Validation)                            │
│     ├── 여러 소스에서 같은 사업자등록번호 확인              │
│     └── 동일 사업주의 다른 점포 확인                        │
└─────────────────────────────────────────────────────────────┘
```

**필요 API**:
- 국세청 사업자등록번호 진위확인 (공공데이터포털)
- 각 인증기관 검증 API

### 3. 사진 분류 시스템

```python
class PhotoCategory(Enum):
    EXTERIOR = "exterior"      # 외부 (간판, 건물)
    INTERIOR = "interior"      # 내부 (홀, 테이블)
    FOOD = "food"              # 음식 사진
    MENU_BOARD = "menu_board"  # 메뉴판
    CERTIFICATE = "certificate" # 인증서/증명서
```

**분류 방법**: 사용자 업로드 시 카테고리 지정

### 4. 식당 정보 앱 필수 기능 (경쟁사 비교)

| 기능 | 배달의민족 | 요기요 | 망고플레이트 | 캐치테이블 |
|------|-----------|--------|-------------|-----------|
| 메뉴/가격 | ✅ | ✅ | ✅ | ✅ |
| 사진 (내부/외부/음식) | ✅ | ✅ | ✅ | ✅ |
| 리뷰/평점 | ✅ | ✅ | ✅ | ✅ |
| 영업시간/휴무일 | ✅ | ✅ | ✅ | ✅ |
| 예약 | ❌ | ❌ | ❌ | ✅ |
| 웨이팅 | ❌ | ❌ | ❌ | ✅ |
| 실시간 혼잡도 | ❌ | ❌ | ❌ | ✅ |
| 알레르기 필터 | ❌ | ❌ | ❌ | ❌ |
| 영양 정보 | ❌ | ❌ | ❌ | ❌ |
| 사업자 정보 공개 | ❌ | ❌ | ❌ | ❌ |

**'바른인증식당'만의 차별점**:
- ✅ 알레르기 필터 (핵심 기능)
- ✅ 영양 정보 (저나트륨 등)
- ✅ 사업자등록증 검증 마크 (신뢰도)
- ✅ 인증 뱃지 공식 검증 (공공데이터 기반)

## 프로젝트 로드맵

| Phase | 내용 | 상태 |
|-------|------|------|
| Phase 1 | 52개 Python 파일 구현 | ✅ 완료 |
| Phase 2 | SQL 마이그레이션 + README | ✅ 완료 |
| Phase 3~7 | (중간 단계) | ✅ 완료 |
| Phase 8-A | E2E 파이프라인 통합 | ✅ 완료 |
| Phase 8-B | TouristInfo 매칭 + 체크포인트 | ✅ 완료 |
| Phase 9-A | 메뉴 보강 최적화 (관광공사 상세 조회 확대) | ⬜ 대기 |
| Phase 9-B | 일반음식점 상태 확인 (영업중/폐업/위생등급) | ⬜ 대기 |
| Phase 9-C | 전체 벤치마크 | ⬜ 대기 |
| Phase 9-D | 자동화 파이프라인 | ⬜ 대기 |
| Phase 10 | 사업자등록증 검증 시스템 | ⬜ 대기 |
| Phase 11 | 사진 분류 시스템 | ⬜ 대기 |
| Phase 12 | 영업시간/휴무일 보강 | ⬜ 대기 |
| Phase 13 | 예약/웨이팅 시스템 (향후) | ⬜ 대기 |