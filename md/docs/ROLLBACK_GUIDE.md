# docs/ROLLBACK_GUIDE.md

## 마이그레이션 롤백 가이드

### 롤백이 필요한 상황
- 마이그레이션 실행 후 앱 오류 발생
- 데이터 무결성 위반 발견
- 성능 저하 (인덱스 문제)

### 롤백 절차

#### 1. 현재 마이그레이션 버전 확인
\`\`\`bash
supabase db remote commits
\`\`\`

#### 2. 롤백할 마이그레이션 파일 수정
- 실패한 마이그레이션의 역변환 SQL 작성
- 파일명: `006_rollback_005_operational_status.sql`

#### 3. 역변환 SQL 예시
\`\`\`sql
-- 005 롤백: operational_status 컬럼 제거
ALTER TABLE public.restaurants DROP COLUMN IF EXISTS operational_status;
DROP INDEX IF EXISTS idx_restaurants_operational;
\`\`\`

#### 4. 롤백 배포
\`\`\`bash
supabase db push
\`\`\`

### 주의사항
- 데이터 손실 가능성이 있는 롤백은 반드시 백업 후 실행
- 롤백 전 `supabase db dump`로 현재 스키마 백업
\`\`\`bash
supabase db dump > backup_$(date +%Y%m%d_%H%M%S).sql
\`\`\`