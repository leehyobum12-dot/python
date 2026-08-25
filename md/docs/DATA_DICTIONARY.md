# 바른인증식당 Data Dictionary (SSOT)

> 최종 갱신: 2026-08-23 | 스키마 버전: v5.1
> 규칙: 영문 컬럼명 ↔ 한글 의미는 반드시 이 문서에서 1:1 매핑

규칙 1: DB 컬럼명 = 영문 snake_case (단일 언어, 국제 표준)
규칙 2: Data Dictionary 문서는 4열 테이블로 명확히 분리
       [영문 컬럼명] | [한글 의미] | [타입] | [예시]
규칙 3: SQL COMMENT는 한글 (팀 협업 용이)
규칙 4: 코드 내 주석은 한글, 타입 힌팅은 영문
## 1. `restaurants` (Gold Layer — 식당 마스터)

### 1.1 기본 정보
| Column | 의미 | Type | Example |
|--------|------|------|---------|
| `id` | 고유 ID | bigint (auto) | 12345 |
| `dedup_key` | 중복제거 키 (Unique) | text | `sha256:a1b2c3...` |
| `store_name` | 상호명 | text | `제주옥돔구이` |
| `data_source` | 최초 수집 소스 | text | `food_safety` |

### 1.2 주소 (3계층 표준화)
| Column | 의미 | Type | Example |
|--------|------|------|---------|
| `province` | 시도 | text | `제주특별자치도` |
| `municipality` | 시군구 | text | `서귀포시` |
| `emd` | 읍면동 | text | `서홍동` |
| `region` | 통합키 (Generated) | text | `제주특별자치도 서귀포시 서홍동` |
| `address_road` | 도로명주소 | text | `하예하동로16번길 11-1` |
| `address_lotnum` | 지번주소 | text | `123-4` |

### 1.3 지리 좌표
| Column | 의미 | Type | Example |
|--------|------|------|---------|
| `lat` | 위도 (WGS84) | double | 33.2523 |
| `lng` | 경도 (WGS84) | double | 126.5591 |
| `geog` | PostGIS 지리좌표 (트리거 자동) | geography | — |

### 1.4 메뉴 & 가격 (Phase 11 준비)
| Column | 의미 | Type | Example |
|--------|------|------|---------|
| `main_menu` | 대표메뉴 | text | `옥돔구이` |
| `main_menu_source` | 대표메뉴 출처 | text | `tourist_info` |
| `menus` | 메뉴 배열 (Phase 11: jsonb[]로 전환) | text[] | `{옥돔구이, 고등어조림}` |
| `menus_source` | 메뉴 출처 | text | `tourist_info` |

### 1.5 영업 상태 (v5.1 신규)
| Column | 의미 | Type | Example |
|--------|------|------|---------|
| `operational_status` | **운영상태 (3-state)** | text (enum) | `operational` |
| `is_closed` | 폐업 여부 (하위호환) | boolean | false |
| `closed_at` | 폐업일 | date | `2026-03-15` |
| `is_verified` | 인증 여부 | boolean | true |
| `last_verified_at` | 최종 검증 시각 | timestamptz | `2026-08-23T10:00:00Z` |

### 1.6 영업 시간 구조 (business_hours jsonb)
```json
{
  "open": "11:00",
  "break_start": "15:00",
  "break_end": "17:00",
  "close": "22:00",
  "last_order": "21:00",
  "holidays": ["monday", "sunday"]
}

### 1.7 사진 구조 (photos jsonb[])
[
  {
    "url": "https://...",
    "category": "unknown",  // exterior|interior|food|menu_board|certificate|unknown
    "source": "tourist_info",
    "fetched_at": "2026-08-23T00:00:00"
  }
]

### 1.8 태그 (7차원)
cuisine_tags - 요리 종류 (한식, 중식...)
nutrient_tags - 영양 (저나트륨, 고단백...)
cooking_tags - 조리법 (구이, 찜...)
ingredient_tags - 재료
regional_tags - 지역 특색
allergen_tags - 알레르기 유발
occasion_tags - 상황 (회식, 가족...)
avoid_tags - 회피 태그 (사용자 건강 기반)

### 2. raw_certifications (Bronze Layer — 원본 보존)

Column    |    의미          |  Type
source       수집 어댑터         text
batch_id     배치 ID            text
source_key   소스 원본 PK        text
raw          원본 jsonb (불변)   jsonb
collected_at 수집 시각           timestamptz

### 3. 운영상태 Enum 정의
operational         → 정상 영업
temporarily_closed  → 일시 휴업 (리모델링, 여름휴가)
permanently_closed  → 폐업

### 4. 향후 수정 로드맵
Phase     |    항목        |    현재 → 변경
11           메뉴 가격         menus text[] → menus jsonb[]
11           사진 정규화       photos jsonb → restaurant_photos 테이블 분리
12           사진 자동 분류    로컬 CLIP 모델 (과금 0)
13           실시간 영업 상태  RPC is_open_now() 추가

### 2. `sql.migrations/005_operational_status.sql`

```sql
-- ============================================================
-- Migration: operational_status 3-state enum 추가 (v5.1)
-- 목적: 일시 휴업(temporarily_closed) 구분, is_closed 한계 극복
-- ============================================================

-- Step 1: 신규 컬럼 추가 (기본값: operational)
ALTER TABLE public.restaurants
  ADD COLUMN IF NOT EXISTS operational_status text DEFAULT 'operational'
  CHECK (operational_status IN (
    'operational',
    'temporarily_closed',
    'permanently_closed'
  ));

-- Step 2: 기존 is_closed 데이터 마이그레이션
UPDATE public.restaurants
SET operational_status = CASE
  WHEN is_closed = true THEN 'permanently_closed'
  ELSE 'operational'
END;

-- Step 3: 인덱스 (영업중 + 인증된 식당 빠른 조회)
CREATE INDEX IF NOT EXISTS idx_restaurants_operational
  ON public.restaurants(operational_status)
  WHERE operational_status = 'operational' AND is_verified = true;

-- Step 4: bulk_upsert_restaurants RPC 업데이트 (operational_status 병합)
CREATE OR REPLACE FUNCTION public.bulk_upsert_restaurants(payload jsonb)
RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE affected integer;
BEGIN
  CREATE TEMP TABLE tmp_rests ON COMMIT DROP AS
  SELECT * FROM jsonb_to_recordset(payload) AS x(
    store_name text, province text, municipality text, emd text,
    address_road text, address_lotnum text,
    main_menu text, main_menu_source text,
    menus text[], menus_source text, quality_score smallint,
    lat double precision, lng double precision,
    phone text, biz_type text, certs text[],
    data_source text, designated_at date, dedup_key text,
    cuisine_tags text[], nutrient_tags text[], cooking_tags text[],
    ingredient_tags text[], regional_tags text[], allergen_tags text[],
    occasion_tags text[], avoid_tags text[],
    hygiene_grade text, closed_at date,
    business_hours jsonb, photos jsonb,
    operational_status text  -- 🆕
  );

  INSERT INTO public.restaurants AS r (
    store_name, province, municipality, emd, address_road, address_lotnum,
    main_menu, main_menu_source, menus, menus_source, quality_score,
    lat, lng, phone, biz_type, certs, data_source, designated_at, dedup_key,
    cuisine_tags, nutrient_tags, cooking_tags, ingredient_tags,
    regional_tags, allergen_tags, occasion_tags, avoid_tags,
    hygiene_grade, closed_at, business_hours, photos,
    operational_status, is_verified, is_closed, last_verified_at
  )
  SELECT store_name, province, municipality, emd, address_road, address_lotnum,
         main_menu, main_menu_source, COALESCE(menus, '{}'), menus_source, COALESCE(quality_score, 0),
         lat, lng, phone, biz_type, COALESCE(certs, '{}'), data_source, designated_at, dedup_key,
         COALESCE(cuisine_tags, '{}'), COALESCE(nutrient_tags, '{}'), COALESCE(cooking_tags, '{}'),
         COALESCE(ingredient_tags, '{}'), COALESCE(regional_tags, '{}'), COALESCE(allergen_tags, '{}'),
         COALESCE(occasion_tags, '{}'), COALESCE(avoid_tags, '{}'),
         hygiene_grade, closed_at, COALESCE(business_hours, '{}'), COALESCE(photos, '[]'),
         COALESCE(operational_status, 'operational'),
         true, false, now()
  FROM tmp_rests
  ON CONFLICT (dedup_key) WHERE dedup_key IS NOT NULL
  DO UPDATE SET
    certs = (SELECT ARRAY(SELECT DISTINCT unnest(COALESCE(r.certs,'{}') || COALESCE(EXCLUDED.certs,'{}')))),
    cuisine_tags    = (SELECT ARRAY(SELECT DISTINCT unnest(COALESCE(r.cuisine_tags,'{}') || COALESCE(EXCLUDED.cuisine_tags,'{}')))),
    nutrient_tags   = (SELECT ARRAY(SELECT DISTINCT unnest(COALESCE(r.nutrient_tags,'{}') || COALESCE(EXCLUDED.nutrient_tags,'{}')))),
    cooking_tags    = (SELECT ARRAY(SELECT DISTINCT unnest(COALESCE(r.cooking_tags,'{}') || COALESCE(EXCLUDED.cooking_tags,'{}')))),
    ingredient_tags = (SELECT ARRAY(SELECT DISTINCT unnest(COALESCE(r.ingredient_tags,'{}') || COALESCE(EXCLUDED.ingredient_tags,'{}')))),
    regional_tags   = (SELECT ARRAY(SELECT DISTINCT unnest(COALESCE(r.regional_tags,'{}') || COALESCE(EXCLUDED.regional_tags,'{}')))),
    allergen_tags   = (SELECT ARRAY(SELECT DISTINCT unnest(COALESCE(r.allergen_tags,'{}') || COALESCE(EXCLUDED.allergen_tags,'{}')))),
    occasion_tags   = (SELECT ARRAY(SELECT DISTINCT unnest(COALESCE(r.occasion_tags,'{}') || COALESCE(EXCLUDED.occasion_tags,'{}')))),
    avoid_tags      = (SELECT ARRAY(SELECT DISTINCT unnest(COALESCE(r.avoid_tags,'{}') || COALESCE(EXCLUDED.avoid_tags,'{}')))),
    menus = (SELECT ARRAY(SELECT DISTINCT unnest(COALESCE(r.menus,'{}') || COALESCE(EXCLUDED.menus,'{}')))),
    menus_source = COALESCE(EXCLUDED.menus_source, r.menus_source),
    phone = COALESCE(EXCLUDED.phone, r.phone),
    main_menu = CASE WHEN EXCLUDED.main_menu IS NULL THEN r.main_menu
                     WHEN r.main_menu IS NULL THEN EXCLUDED.main_menu
                     WHEN length(EXCLUDED.main_menu) > length(r.main_menu) THEN EXCLUDED.main_menu
                     ELSE r.main_menu END,
    main_menu_source = CASE WHEN EXCLUDED.main_menu IS NULL THEN r.main_menu_source
                            WHEN r.main_menu IS NULL THEN EXCLUDED.main_menu_source
                            WHEN length(EXCLUDED.main_menu) > length(r.main_menu) THEN EXCLUDED.main_menu_source
                            ELSE r.main_menu_source END,
    quality_score = GREATEST(COALESCE(EXCLUDED.quality_score, 0), COALESCE(r.quality_score, 0)),
    biz_type = COALESCE(EXCLUDED.biz_type, r.biz_type),
    address_road = COALESCE(EXCLUDED.address_road, r.address_road),
    address_lotnum = COALESCE(EXCLUDED.address_lotnum, r.address_lotnum),
    lat = COALESCE(EXCLUDED.lat, r.lat),
    lng = COALESCE(EXCLUDED.lng, r.lng),
    hygiene_grade = COALESCE(EXCLUDED.hygiene_grade, r.hygiene_grade),
    closed_at = COALESCE(EXCLUDED.closed_at, r.closed_at),
    business_hours = CASE WHEN jsonb_array_length(COALESCE(EXCLUDED.business_hours, '{}')) > 0
                          THEN EXCLUDED.business_hours ELSE r.business_hours END,
    photos = CASE WHEN jsonb_array_length(COALESCE(EXCLUDED.photos, '[]')) > 0
                  THEN EXCLUDED.photos ELSE r.photos END,
    -- 🆕 operational_status 병합 (폐업 정보는 우선)
    operational_status = CASE
      WHEN EXCLUDED.closed_at IS NOT NULL THEN 'permanently_closed'
      WHEN EXCLUDED.operational_status = 'permanently_closed' THEN 'permanently_closed'
      WHEN EXCLUDED.operational_status = 'temporarily_closed' THEN 'temporarily_closed'
      ELSE r.operational_status
    END,
    is_closed = CASE WHEN EXCLUDED.closed_at IS NOT NULL
                     OR EXCLUDED.operational_status = 'permanently_closed'
                     THEN true ELSE r.is_closed END,
    last_verified_at = now()
  WHERE r.certs IS DISTINCT FROM EXCLUDED.certs
     OR r.lat IS DISTINCT FROM EXCLUDED.lat OR r.lng IS DISTINCT FROM EXCLUDED.lng
     OR r.main_menu IS DISTINCT FROM EXCLUDED.main_menu
     OR r.menus IS DISTINCT FROM EXCLUDED.menus
     OR r.quality_score IS DISTINCT FROM EXCLUDED.quality_score
     OR r.avoid_tags IS DISTINCT FROM EXCLUDED.avoid_tags
     OR r.hygiene_grade IS DISTINCT FROM EXCLUDED.hygiene_grade
     OR r.closed_at IS DISTINCT FROM EXCLUDED.closed_at
     OR r.operational_status IS DISTINCT FROM EXCLUDED.operational_status;

  GET DIAGNOSTICS affected = ROW_COUNT;
  RETURN affected;
END; $$;

GRANT EXECUTE ON FUNCTION public.bulk_upsert_restaurants(jsonb) TO service_role;

-- Step 5: 컬럼 주석
COMMENT ON COLUMN public.restaurants.operational_status IS '운영상태 (operational|temporarily_closed|permanently_closed)';

-- Step 6: View 업데이트
CREATE OR REPLACE VIEW public.restaurants_public WITH (security_invoker = true) AS
SELECT id, store_name, biz_type, phone,
       address_road, address_lotnum,
       province, municipality, emd, region,
       lat, lng, main_menu, main_menu_source,
       menus, menus_source, quality_score,
       est_sodium_mg, is_measurable, certs, naver_url,
       business_days, open_time, close_time, closed_days,
       cuisine_tags, nutrient_tags, cooking_tags, ingredient_tags,
       regional_tags, allergen_tags, occasion_tags,
       dedup_key, data_source, designated_at,
       operational_status,  -- 🆕
       created_at, updated_at, last_verified_at
FROM public.restaurants
WHERE is_verified = true
  AND is_closed = false
  AND operational_status IN ('operational', 'temporarily_closed');

ANALYZE public.restaurants;
NOTIFY pgrst, 'reload schema';
-------------------------------------
#   필드     결정            근거
1  음식가격  ⚠️ Schema만 준비, Phase 11까지 적재 유보
Tourist/공공 API에 가격 데이터 없음. 사장님 직접 입력이 유일한 SSOT. menus를 text[] → jsonb[]({"name":"김치찌개","price":9000})으로 전환 준비
2
영업시간 상세
✅ business_hours jsonb 구조 표준화
컬럼 추가 대신 jsonb 내부 구조 고정: {open, break_start, break_end, close, last_order}. 유연성 유지 + 구조화 양립
3
오늘 영업중
🚫 DB 저장 금지 (계산 필드)
저장하면 다음날 stale data. 앱/RPC에서 실시간 계산 (is_open_now()).
4
운영상태 3-state
✅ operational_status enum 추가
is_closed(boolean)의 한계 극복. temporarily_closed(리모델링/여름휴가) 구분 필수