#!/usr/bin/env python3
"""
단일 시도 테스트 스크립트
==========================
- 전국 재실행 전에 공식 API enrichment 검증용
- 완료 마커 무시 (항상 재실행)

기존 참고:
- test_jeju.py 전체 구조

사용법:
    python scripts/test_single_province.py 제주특별자치도
"""
from __future__ import annotations

import sys
import os
import time

# 경로 설정
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
SRC_DIR = os.path.join(ROOT_DIR, 'src')

if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from bareun_pipeline.bronze.collect import BronzeCollector
from bareun_pipeline.settings import get_settings
from bareun_pipeline.observability.logging import setup_logging, get_logger


def test_province(province: str) -> None:
    """단일 시도 테스트"""
    settings = get_settings()
    logger = get_logger(__name__)

    # 디렉토리 생성
    settings.ensure_directories()

    output_path = os.path.join(settings.gold_dir, f'{province}_test.csv')

    print('=' * 60)
    print(f'🧪 {province} 단일 시도 테스트')
    print('=' * 60)
    print(f'출력 파일: {output_path}')
    print()

    start = time.time()

    try:
        collector = BronzeCollector()
        result = collector.collect_province(province, force=True)

        elapsed = time.time() - start

        print('\n' + '=' * 60)
        print(f'🎉 {province} 테스트 완료! (소요: {elapsed:.1f}초)')
        print(f'📄 결과: {result}')
        print('=' * 60)

        print('\n📋 검증 포인트:')
        print('   1. 공식 API Enrichment 로그 확인')
        print('      - 메뉴 발견: ___건')
        print('      - 메뉴 없음 (추측 금지): ___건')
        print('   2. CSV 파일 열어서 main_menu 컬럼 확인')
        print('      - 빈 값(NULL) 다수 → 정상 (추측 제거)')
        print('      - 공식 소스만 채워짐 → 정상')
        print('   3. 쿼터 사용량 확인 (일일 한도 내)')

    except Exception as e:
        print(f'\n🚨 테스트 실패: {type(e).__name__}: {e}')
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('사용법: python scripts/test_single_province.py <province>')
        print('예시: python scripts/test_single_province.py 제주특별자치도')
        sys.exit(1)

    setup_logging(level=20, json_output=False)  # INFO, 콘솔 출력
    test_province(sys.argv[1])