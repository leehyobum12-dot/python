# test_tourist_detail_expansion.py
"""
Phase 9-A 검증: TouristInfo 상세 조회 100건 확대 테스트
"""

import sys
import time

from bareun_pipeline.adapters.tourist_info import TouristInfoAdapter
from bareun_pipeline.observability.quota import QuotaManager

sys.stdout.reconfigure(line_buffering=True)

print('=' * 60, flush=True)
print('Phase 9-A: TouristInfo 상세 조회 확대 (100건)', flush=True)
print('=' * 60, flush=True)

# 1. QuotaManager 초기화
qm = QuotaManager.get_instance()
print(f"\n[1] 초기 쿼터: {qm.get_remaining('tourist_info')}건", flush=True)

# 2. TouristInfoAdapter 초기화 (max_detail_count=100)
adapter = TouristInfoAdapter(max_detail_count=100)
print(f'   max_detail_count: {adapter.max_detail_count}', flush=True)

# 3. 수집 실행
start = time.time()
records = adapter.collect(province='제주특별자치도')
elapsed = time.time() - start

print('\n[2] 수집 결과:', flush=True)
print(f'   - 총 레코드: {len(records)}건', flush=True)
print(f'   - 소요 시간: {elapsed:.1f}초', flush=True)

# ── 4. 상세 정보 분석 (🆕 수정) ──
with_details = 0
with_menus = 0
with_photos = 0
with_coords = 0
with_hours = 0
with_tel = 0

for r in records:
    # 🆕 external_info 추출
    ext = r.raw.get('external_info', {}) if r.raw else {}

    # 상세 정보 보유 (intro 또는 detail이 있으면)
    has_detail = ext.get('photos') or ext.get('business_hours') or ext.get('rest_days')
    if has_detail:
        with_details += 1

    # 🆕 메뉴 보유 (main_menu 또는 menus)
    if r.main_menu or r.menus:
        with_menus += 1

    # 🆕 사진 보유 (external_info.photos)
    if ext.get('photos'):
        with_photos += 1

    # 좌표 보유
    if r.has_coordinate():
        with_coords += 1

    # 영업시간 보유
    if ext.get('business_hours'):
        with_hours += 1

    # 전화번호 보유
    if r.phone:
        with_tel += 1

print('\n[3] 상세 정보 분석:', flush=True)
print(
    f'   - 상세 정보 보유: {with_details}건 ({with_details/max(1,len(records))*100:.1f}%)',
    flush=True,
)
print(f'   - 메뉴 보유: {with_menus}건 ({with_menus/max(1,len(records))*100:.1f}%)', flush=True)
print(f'   - 사진 보유: {with_photos}건 ({with_photos/max(1,len(records))*100:.1f}%)', flush=True)
print(f'   - 좌표 보유: {with_coords}건 ({with_coords/max(1,len(records))*100:.1f}%)', flush=True)
print(f'   - 영업시간 보유: {with_hours}건 ({with_hours/max(1,len(records))*100:.1f}%)', flush=True)
print(f'   - 전화번호 보유: {with_tel}건 ({with_tel/max(1,len(records))*100:.1f}%)', flush=True)

# 5. 쿼터 소비 확인
print('\n[4] 쿼터 소비:', flush=True)
print(f"   - 남은 쿼터: {qm.get_remaining('tourist_info')}건", flush=True)
print(f"   - 소비 쿼터: {1000 - qm.get_remaining('tourist_info')}건", flush=True)
print(f'   - 요약: {qm.summary()}')

# 6. 샘플 출력 (상세 정보 포함된 상위 5건)
print('\n[5] 샘플 레코드 (상세 정보 보유 상위 5건):', flush=True)
sample_count = 0
for r in records:
    if r.raw and (r.raw.get('detail') or r.raw.get('intro')):
        sample_count += 1
        if sample_count <= 5:
            print(f'\n  {sample_count}. {r.raw_name}', flush=True)
            print(f'     주소: {r.raw_address}', flush=True)
            print(f"     메뉴: {r.main_menu or '(없음)'}", flush=True)
            if r.menus:
                print(f'     메뉴목록: {r.menus[:3]}...', flush=True)
            if r.raw.get('detail', {}).get('tel'):
                print(f"     전화: {r.raw['detail']['tel']}", flush=True)
            if r.raw.get('intro', {}).get('usetime'):
                print(f"     영업시간: {r.raw['intro']['usetime'][:50]}...", flush=True)

print('\n' + '=' * 60, flush=True)
if with_details >= 50:
    print('✅ Phase 9-A 성공: 상세 정보 50건 이상 확보', flush=True)
else:
    print('⚠️  Phase 9-A 부분 성공: 상세 정보 부족 (API 확인 필요)', flush=True)
print('=' * 60, flush=True)
