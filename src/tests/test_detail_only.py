"""
Phase 9-A: 상세 조회 전용 테스트
=================================
- 목록 조회 쿼터 절약 (0건)
- 기존 체크포인트의 content_id로 상세만 조회
- 목표: 상세 조회 100건 성공 확인

쿼터 소비: 100건 × 2 (common + intro) = 200건
"""

import time

from bareun_pipeline.adapters.base import BaseAdapter
from bareun_pipeline.adapters.tourist_info.client import TouristInfoClient
from bareun_pipeline.adapters.tourist_info.mapper import map_to_record
from bareun_pipeline.bronze.tourist_content_tracker import TouristCheckpoint
from bareun_pipeline.observability.quota import QuotaManager
from bareun_pipeline.settings import get_settings

print('=' * 70)
print('Phase 9-A: TouristInfo 상세 조회 전용 테스트')
print('=' * 70)

# ── 1. 초기화 ──
settings = get_settings()
quota = QuotaManager.get_instance()
checkpoint = TouristCheckpoint(settings.cache_dir)

print('\n[1] 초기 상태:')
print(f"   - tourist_info 쿼터: {quota.get_remaining('tourist_info')} / 1000")
print(f'   - 체크포인트: {len(checkpoint.get_existing_ids())}건')


# ── 2. BaseAdapter를 통해 Client 생성 ──
class TempAdapter(BaseAdapter):
    source = 'test'
    cert_type = 'test'
    quota_provider = None
    pii_fields = set()

    # 인스턴스 생성 및 반환 타입(list) 에러를 방지하기 위해 빈 리스트 반환
    def collect(self, *args, **kwargs):
        return []


temp = TempAdapter()
client = TouristInfoClient(
    session=temp.session,
    settings=settings,
    api_caller=temp._safe_request,
)

# ── 3. 체크포인트에서 content_id 100개 추출 ──
content_ids = sorted(list(checkpoint.get_existing_ids()))[:100]
print(f'\n[2] 테스트 대상: 상위 {len(content_ids)}건')
if content_ids:
    print(f'   - 첫 번째 ID: {content_ids[0]}')
    print(f'   - 마지막 ID: {content_ids[-1]}')
else:
    print('⚠️ 체크포인트에 데이터가 없습니다. 스크립트를 종료합니다.')
    exit()  # 데이터가 없으면 여기서 안전하게 종료

# ── 4. 상세 조회 실행 ──
print('\n[3] 상세 조회 실행 중 (약 1분 소요 예상)...')
start = time.time()

success_detail = 0
success_intro = 0
failed = 0
rich_records = []  # 상세 정보 풍부한 레코드

for i, content_id in enumerate(content_ids):
    try:
        detail = client.fetch_detail_common(content_id)
        intro = client.fetch_detail_intro(content_id)

        if detail:
            success_detail += 1
        if intro:
            success_intro += 1

        # 상세 정보 풍부한 경우 저장 (샘플용)
        if detail or intro:
            # 임시 item 생성 (목록 응답이 없으므로 최소 정보만)
            mock_item = {
                'contentid': str(content_id),
                'title': detail.get('title', f'Unknown_{content_id}'),
                'addr1': detail.get('addr1', ''),
                'addr2': detail.get('addr2', ''),
                'mapy': detail.get('mapy', ''),
                'mapx': detail.get('mapx', ''),
                'firstimage': detail.get('firstimage', ''),
                'firstimage2': detail.get('firstimage2', ''),
            }
            record = map_to_record(mock_item, detail, intro, 'tourist_info', 'tourist_info')
            if record:
                rich_records.append(record)

        # 진행 상황 출력 (10건마다)
        if (i + 1) % 10 == 0:
            elapsed = time.time() - start
            print(
                f'   진행: {i+1}/{len(content_ids)} '
                f'(성공: {success_detail}+{success_intro}, '
                f'실패: {failed}, '
                f'{elapsed:.1f}초)'
            )

        # 상세 조회 간 지연 (API 서버 부하 방지)
        time.sleep(0.5)

    except Exception as e:
        failed += 1
        print(f'   ❌ 실패 {content_id}: {type(e).__name__}: {e}')

elapsed = time.time() - start

# ── 5. 결과 분석 ──
print('\n[4] 상세 조회 결과:')
print(f'   - 소요 시간: {elapsed:.1f}초')
print(f'   - detail 성공: {success_detail}건 ({success_detail/len(content_ids)*100:.1f}%)')
print(f'   - intro 성공: {success_intro}건 ({success_intro/len(content_ids)*100:.1f}%)')
print(f'   - 실패: {failed}건')
print(
    f"   - 쿼터 소비: {quota.DAILY_LIMITS['tourist_info'] - quota.get_remaining('tourist_info')}건"
)

# ── 6. 상세 정보 풍부도 분석 ──
if rich_records:
    print(f'\n[5] 상세 정보 풍부도 (성공한 {len(rich_records)}건 기준):')
    with_menus = sum(1 for r in rich_records if r.main_menu or r.menus)
    with_hours = sum(
        1 for r in rich_records if r.raw and r.raw.get('external_info', {}).get('business_hours')
    )
    with_rest = sum(
        1 for r in rich_records if r.raw and r.raw.get('external_info', {}).get('rest_days')
    )
    with_photos = sum(
        1 for r in rich_records if r.raw and r.raw.get('external_info', {}).get('photos')
    )
    with_coords = sum(1 for r in rich_records if r.has_coordinate())
    with_tel = sum(1 for r in rich_records if r.phone)

    print(f'   - 메뉴 보유: {with_menus}건 ({with_menus/max(1,len(rich_records))*100:.1f}%)')
    print(f'   - 영업시간: {with_hours}건 ({with_hours/max(1,len(rich_records))*100:.1f}%)')
    print(f'   - 휴무일: {with_rest}건 ({with_rest/max(1,len(rich_records))*100:.1f}%)')
    print(f'   - 사진: {with_photos}건 ({with_photos/max(1,len(rich_records))*100:.1f}%)')
    print(f'   - 좌표: {with_coords}건 ({with_coords/max(1,len(rich_records))*100:.1f}%)')
    print(f'   - 전화번호: {with_tel}건 ({with_tel/max(1,len(rich_records))*100:.1f}%)')

# ── 7. 샘플 출력 ──
print('\n[6] 상세 정보 샘플 (상위 5건):')
for i, r in enumerate(rich_records[:5], 1):
    ext = r.raw.get('external_info', {}) if r.raw else {}
    print(f'\n  {i}. {r.raw_name}')
    print(f'     주소: {r.raw_address}')
    if r.main_menu:
        print(f'     대표메뉴: {r.main_menu}')
    if r.menus:
        print(f'     메뉴목록: {r.menus[:5]}')
    if r.phone:
        print(f'     전화: {r.phone}')
    if ext.get('business_hours'):
        print(f"     영업시간: {ext['business_hours']}")
    if ext.get('rest_days'):
        print(f"     휴무일: {ext['rest_days']}")
    if ext.get('photos'):
        print(f"     사진: {len(ext['photos'])}장")
    if r.has_coordinate():
        print(f'     좌표: ({r.lat:.6f}, {r.lng:.6f})')

# ── 8. 최종 판단 ──
print('\n' + '=' * 70)
if success_detail >= 80:
    print('🎉 Phase 9-A 성공: 상세 조회 80건 이상 확보')
    print('   → 상세 조회 로직 정상 작동, 실제 수집 시 메뉴/영업시간 확보 가능')
elif success_detail >= 50:
    print('✅ Phase 9-A 부분 성공: 상세 조회 50건 이상')
    print('   → API 상황에 따라 변동 가능')
else:
    print('⚠️  Phase 9-A 미달: 상세 조회 부족')
    print('   → API 상태 또는 키 권한 재확인 필요')
print('=' * 70)

# ── 9. 최종 쿼터 상태 ──
print('\n[9] 최종 쿼터 상태:')
print(f'   요약: {quota.summary()}')
print(f"   남은 tourist_info: {quota.get_remaining('tourist_info')}건")
