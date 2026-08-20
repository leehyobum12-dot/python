# data_pipeline/run_national.py
"""
전국 16개 시도 자동 수집 및 Enrichment (Checkpoint Resume 지원)
- 쿼터 소진 시 자동 중단, 다음 날 이어서 실행
- 지오코딩 캐시(84,483건) 활용으로 API 비용 0원화
"""
import os
import sys
import time
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
load_dotenv()

from pipeline import run_pipeline, OUT_DIR, PROVINCES

def main():
    print("=" * 70)
    print("🌍 전국 16개 시도 파이프라인 시작 (Checkpoint Resume 모드)")
    print("=" * 70)
    
    total_start = time.time()
    success_count = 0
    
    for i, province in enumerate(PROVINCES, 1):
        output = os.path.join(OUT_DIR, f"{province}.csv")
        
        # 완료 마커 확인 (이미 enrichment가 끝난 시도는 스킵)
        marker_path = os.path.join(OUT_DIR, f".{province}_done")
        if os.path.exists(marker_path):
            print(f"\n⏭️ [{i}/16] {province} 스킵 (이미 완료됨)")
            continue
            
        print(f"\n{'='*70}")
        print(f"🚀 [{i}/16] {province} 수집 및 Enrichment 시작")
        print(f"{'='*70}")
        
        try:
            start = time.time()
            run_pipeline(
                province=province,
                output=output,
                snapshot_food_safety=True  # 전국 스냅샷 활용
            )
            elapsed = time.time() - start
            
            # 완료 마커 생성
            with open(marker_path, 'w') as f:
                f.write(f"completed at {time.ctime()}\n")
                
            print(f"🎉 {province} 완료! (소요: {elapsed:.1f}초)")
            success_count += 1
            
        except Exception as e:
            # 쿼터 소진 등으로 중단된 경우, 로그 남기고 다음 날을 기약
            print(f"🛑 {province} 중단됨: {type(e).__name__}: {e}")
            print("💡 내일 쿼터 리셋 후 다시 실행하세요. (Checkpoint가 이어서 처리)")
            break
            
    total_elapsed = time.time() - total_start
    print(f"\n{'='*70}")
    print(f"🏁 금일 배치 종료: {success_count}개 시도 완료 (총 {total_elapsed:.1f}초)")
    print(f"{'='*70}")

if __name__ == "__main__":
    main()