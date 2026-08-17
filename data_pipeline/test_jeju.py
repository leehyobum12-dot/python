# data_pipeline/test_jeju.py
"""
제주도 단일 시도 테스트 스크립트
- 전국 재실행 전에 공식 API enrichment 검증용
- 완료 마커 무시 (항상 재실행)
"""
import os
import sys
import time
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
load_dotenv()

from pipeline import run_pipeline, OUT_DIR

def main():
    province = "제주특별자치도"
    output = os.path.join(OUT_DIR, f"{province}_test.csv")

    print("=" * 60)
    print(f"🧪 제주도 단일 시도 테스트 (공식 API enrichment 검증)")
    print("=" * 60)
    print(f"출력 파일: {output}")
    print()

    start = time.time()

    try:
        run_pipeline(
            province=province,
            output=output,
            snapshot_food_safety=True,  # nationwide_latest.json 사용
        )
    except Exception as e:
        print(f"\n🚨 테스트 실패: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return

    elapsed = time.time() - start
    print(f"\n{'=' * 60}")
    print(f"🎉 제주도 테스트 완료! (소요: {elapsed:.1f}초)")
    print(f"📄 CSV 확인: {output}")
    print(f"{'=' * 60}")

    print("\n📋 검증 포인트:")
    print("   1. 공식 API Enrichment 로그 확인")
    print("      - 메뉴 발견 (네이버): ___건")
    print("      - 메뉴 없음 (추측 금지): ___건")
    print("      - 유령 영업 확인: ___건")
    print("   2. CSV 파일 열어서 main_menu 컬럼 확인")
    print("      - 빈 값(NULL) 다수 → 정상 (추측 제거)")
    print("      - 공식 소스만 채워짐 → 정상")
    print("   3. 쿼터 사용량 확인 (일일 한도 내)")


if __name__ == '__main__':
    main()