# 📦 바른인증식당 프로젝트 인계 번들 (Review Bundle)

> **최종 업데이트:** 2026-08-23  
> **인계 대상:** 신규 개발자 / 유지보수 담당자  
> **현재 Phase:** Phase 8-B 완료 → Phase 9 진입 준비

---

## 1. 프로젝트 개요

| 항목 | 내용 |
|------|------|
| 프로젝트명 | 바른인증식당 (Bareun Restaurant) |
| 미션 | 공공데이터 간 정보 비대칭 해소, 국민 행복 증진 |
| 타겟 | 한국 소상공인 및 소비자 |
| 예산 | 월 10만원 이하 (Free-Tier 극대화, API 과금 차단 필수) |
| 기술 스택 | Python 3.11+, Pydantic v2, Supabase(PostGIS, RLS), Vercel, Spacy(NER) |
| 아키텍처 | Medallion 3계층 (Bronze → Silver → Gold) |

---

## 2. 아키텍처

```
[Adapters] → [Bronze] → [Silver] → [Gold] → [Supabase]
 수집         원본 보존   정제/병합   변환/출력   적재
```

### 데이터 소스 현황

| 소스 | 어댑터 | 제주 건수 | 비고 |
|------|--------|-----------|------|
| 안심식당 | `SafeRestaurantAdapter` | ~1,123건 | ✅ 안정 |
| 저나트륨 | `LowSodiumAdapter` | ~71건 | ✅ 안정 |
| 모범음식점 | `ModelRestaurantAdapter` | ~415건 | ✅ 안정 (로컬 CSV) |
| 위생등급 | `FoodSafetyAdapter` | ~45건 | ⚠️ 불안정 (재플레이 로직) |
| 관광공사 | `TouristInfoAdapter` | ~716건 | ✅ 체크포인트 구현됨 |
| 일반음식점 | `GeneralRestaurantAdapter` | ~205건/회 | ⚠️ 전체 229만건 중 일부 |

---

## 3. 파일 구조

```
src/bareun_pipeline/
├── adapters/
│   ├── base.py                  # BaseAdapter, CircuitBreaker, parse_addr_tokens
│   ├── safe_restaurant.py       # 안심식당
│   ├── low_sodium.py            # 저나트륨
│   ├── model_restaurant.py      # 모범음식점 (로컬 CSV)
│   ├── food_safety.py           # 위생등급 (불안정, 재플레이 로직)
│   ├── tourist_info.py          # 관광공사 (체크포인트 구현됨)
│   └── general_restaurant.py    # 일반음식점 (상태 확인용)
├── bronze/
│   ├── raw_archiver.py          # 원본 JSONL.gz 아카이브
│   └── tourist_checkpoint.py    # content_id 기반 체크포인트
├── silver/
│   ├── normalize.py             # 3계층 주소 정규화
│   ├── enrich.py                # 지오코딩, 메뉴, 가게정보 보강
│   ├── merge.py                 # dedup_key + 멀티 키 스코어 병합
│   └── classify.py              # 7차원 태그 분류
├── gold/
│   ├── transform.py             # Gold 변환
│   └── export.py                # NDJSON + CSV 내보내기
└── domain/
    ├── record.py                # Pydantic CertRecord (SSOT)
    ├── dedup.py                 # SHA256 dedup_key 생성
    └── avoid_tags.py            # 알레르기 기반 회피 태그 자동 생성

scripts/
├── e2e_sample_test.py           # E2E 통합 테스트
├── test_matching_radius.py      # 매칭 반경 진단
└── verify_data_quality.py       # 품질 검증
```

---

## 4. 완료된 작업

### Phase 1~7: 기반 파이프라인 ✅
- 수집 어댑터 6종
- Bronze 원본 아카이브 (JSONL.gz)
- Silver 정규화 (3계층 주소: si, sigungu, emd)
- 네이버 지오코딩 (캐시 84,000+ 엔트리, 서킷 브레이커)
- 병합: dedup_key 기반
- 7차원 태그 분류
- Gold 변환 + NDJSON/CSV 내보내기

### Phase 8-A: E2E 파이프라인 통합 ✅
- `e2e_sample_test.py` 통합 테스트
- 전체 파이프라인 정상 동작 확인

### Phase 8-B: TouristInfo 매칭 + 체크포인트 ✅
- `tourist_checkpoint.py`: content_id 기반 증분 업데이트
- `geocode_batch(force=True)`: 좌표 강제 갱신
- 멀티 키 스코어 매칭 구현 (이름 50% + 주소 30% + 좌표 20%)
- 매칭 임계값: 60% 이상

---

## 5. 핵심 설계 결정

| 결정 | 이유 |
|------|------|
| 네이버 지오코딩 = 좌표 단일 진실 공급원 | 좌표계 불일치 해소 (공공데이터 필지 중심 ↔ 네이버 건물 중심) |
| 일반음식점 229만건 지오코딩 생략 | 토큰/비용 절약, 상태 확인만 수행 |
| 메뉴의 단일 진실 공급원 = 관광공사 | 네이버/카카오 로컬 검색은 메뉴 정보 미제공 |
| 매칭 임계값 60% | 오탐 방지 + 좌표 오차 허용 |
| 체크포인트 증분 업데이트 | 일일 트래픽 1,000건 제한 대응 |

---

## 6. 알려진 문제점

| 문제 | 심각도 | 원인 | 상태 |
|------|--------|------|------|
| 메뉴 보강 0건 | 🔴 | 네이버/카카오 로컬 검색은 메뉴 미제공 | 미해결 |
| TouristInfo 매칭 0건 | 🔴 | 샘플 100건 한계 (실제 다른 식당) | 미해결 |
| 일반음식점 상태 확인 미구현 | 🟡 | 영업중/폐업/위생등급 확인 미구현 | 미해결 |
| FoodSafety API 불안정 | 🟡 | datago/mfds 모두 불안정 | 재플레이로 대응 |

---

## 7. 남은 작업 (로드맵)

| Phase | 내용 | 우선순위 |
|-------|------|----------|
| 9-A | 메뉴 보강 최적화 (관광공사 상세 조회 30→100건 확대) | 🔴 높음 |
| 9-B | 일반음식점 상태 확인 (영업중/폐업/위생등급 병합) | 🔴 높음 |
| 9-C | 전체 2575건 벤치마크 + 체크포인트 2회차 테스트 | 🟡 중간 |
| 9-D | 자동화 파이프라인 (GitHub Actions + Vercel Cron) | 🟡 중간 |
| 10 | 사업자등록증 검증 시스템 | 🟢 낮음 |
| 11 | 사진 분류 시스템 | 🟢 낮음 |
| 12 | 영업시간/휴무일 보강 강화 | 🟢 낮음 |
| 13 | 예약/웨이팅 시스템 | 🟢 낮음 |

---

## 8. 환경 설정

### `.env` 파일

```
DATA_GO_KR_KEY=공공데이터포털_서비스키
NAVER_CLIENT_ID=네이버_클라이언트_ID
NAVER_CLIENT_SECRET=네이버_클라이언트_시크릿
KAKAO_REST_API_KEY=카카오_REST_API_키
SUPABASE_URL=Supabase_URL
SUPABASE_SERVICE_ROLE_KEY=Supabase_서비스_역할_키
```

### 설치

```bash
git clone <repo-url>
cd bareun/app/python
uv venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
uv pip install -e ".[dev]"
cp .env.example .env
```

---

## 9. 실행 방법

```bash
# E2E 통합 테스트 (제주도, 100건 샘플)
python scripts/e2e_sample_test.py

# 옵션
python scripts/e2e_sample_test.py --sample-size 2575
python scripts/e2e_sample_test.py --no-geocoding
python scripts/e2e_sample_test.py --no-tourist-info
python scripts/e2e_sample_test.py --no-general-restaurant
python scripts/e2e_sample_test.py --skip-food-safety

# 매칭 반경 진단
python scripts/test_matching_radius.py

# 품질 검증
python scripts/verify_data_quality.py <csv_path>
```

---

## 10. 데이터 스키마 (CertRecord)

```python
class CertRecord(BaseModel):
    # 식별자
    source: str                    # 어댑터 식별자
    cert_type: str                 # 인증 타입
    dedup_key: Optional[str]       # SHA256 중복 제거 키

    # 기본 정보
    raw_name: str
    raw_address: str

    # 주소 3계층
    province: Optional[str]        # 시/도
    municipality: Optional[str]    # 시/군/구
    emd: Optional[str]             # 읍/면/동
    road_name: Optional[str]       # 도로명 주소
    lot_num: Optional[str]         # 지번 주소

    # 연락처/업종
    phone: Optional[str]
    biz_type: Optional[str]

    # 메뉴
    main_menu: Optional[str]
    main_menu_source: Optional[str]
    menus: list[str]
    menus_source: Optional[str]

    # 인증
    certs: list[str]
    designated_at: Optional[date]
    cancelled_at: Optional[date]
    closed_at: Optional[date]
    is_active: bool = True

    # 좌표
    lat: Optional[float]
    lng: Optional[float]
    coord_confidence: Optional[str]

    # 7차원 태그
    cuisine_tags: list[str]
    nutrient_tags: list[str]
    cooking_tags: list[str]
    ingredient_tags: list[str]
    regional_tags: list[str]
    allergen_tags: list[str]
    occasion_tags: list[str]
    avoid_tags: list[str]

    # 품질/메타
    store_name_quality: Optional[str]
    quality_score: int = 0
    ghost_suspicion_score: int = 0
    data_source: Optional[str]

    # 원본 데이터
    raw: Optional[dict]
```

---

## 11. 경쟁사 비교 (참고)

| 기능 | 배달의민족 | 요기요 | 망고플레이트 | 캐치테이블 | 바른인증식당 |
|------|-----------|--------|-------------|-----------|-------------|
| 메뉴/가격 | ✅ | ✅ | ✅ | ✅ | 🔄 진행중 |
| 사진 | ✅ | ✅ | ✅ | ✅ | 🔄 진행중 |
| 리뷰/평점 | ✅ | ✅ | ✅ | ✅ | ❌ |
| 예약/웨이팅 | ❌ | ❌ | ❌ | ✅ | ❌ |
| 알레르기 필터 | ❌ | ❌ | ❌ | ❌ | ✅ (핵심) |
| 영양 정보 | ❌ | ❌ | ❌ | ❌ | ✅ (핵심) |
| 사업자 검증 | ❌ | ❌ | ❌ | ❌ | ✅ (계획) |
| 인증 뱃지 공식 검증 | ❌ | ❌ | ❌ | ❌ | ✅ (핵심) |

---

## 12. 인계 시 주의사항

1. **네이버 지오코딩 캐시**는 `data/cache/geocode/geocode_cache.json`에 저장됩니다. 삭제 시 재수집됩니다.
2. **관광공사 체크포인트**는 `data/cache/tourist_checkpoint.json`에 저장됩니다. 삭제 시 전체 재수집됩니다.
3. **FoodSafety API**는 불안정하므로 재플레이 로직이 필요합니다.
4. **일반음식점 229만건**은 전체 수집 시 토큰/시간 소모가 크므로, 필요한 지역만 수집하도록 설계되어 있습니다.