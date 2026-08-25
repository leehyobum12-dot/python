"""
Phase 9-A 최종 검증을 위한 완전 리셋
- QuotaManager 리셋
- CircuitBreaker 리셋
- TouristCheckpoint 리셋 (content_ids 비우기)
"""

from bareun_pipeline.adapters import get_all_adapters
from bareun_pipeline.bronze.tourist_content_tracker import TouristCheckpoint
from bareun_pipeline.observability.quota import QuotaManager
from bareun_pipeline.settings import get_settings

print('=' * 60)
print('Phase 9-A 최종 리셋')
print('=' * 60)

# 1. QuotaManager 리셋
qm = QuotaManager.get_instance()
qm.reset()
print(f"✅ QuotaManager 리셋: tourist_info={qm.get_remaining('tourist_info')}건")

# 2. CircuitBreaker 리셋
for AdapterClass in get_all_adapters():
    adapter = AdapterClass()
    if hasattr(adapter, 'circuit_breaker'):
        adapter.circuit_breaker.reset()
print('✅ CircuitBreaker 리셋 완료')

# 3. TouristCheckpoint 리셋 (핵심!)
settings = get_settings()
checkpoint = TouristCheckpoint(settings.cache_dir)
checkpoint.save([])  # 빈 리스트로 저장 → content_ids 비움
print(f'✅ TouristCheckpoint 리셋: {len(checkpoint.get_existing_ids())}건')

# 4. 검증
print('\n📊 최종 상태:')
print(f"   - tourist_info 쿼터: {qm.get_remaining('tourist_info')} / 1000")
print(f'   - 체크포인트: {len(checkpoint.get_existing_ids())}건')
print('   - 재실행 준비 완료!')
print('=' * 60)
