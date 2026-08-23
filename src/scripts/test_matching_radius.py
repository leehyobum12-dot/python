#!/usr/bin/env python3
"""
TouristInfo 매칭 반경 테스트
=============================
여러 반경(100m, 200m, 500m)을 테스트하여 최적값 찾기

사용법:
    1. 먼저 E2E 실행: python scripts/e2e_sample_test.py
    2. 본 스크립트 실행: python scripts/test_matching_radius.py
"""
from __future__ import annotations

import os
import sys
from typing import List

# 경로 설정
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
SRC_DIR = os.path.join(ROOT_DIR, 'src')

if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from dotenv import load_dotenv
load_dotenv()

from bareun_pipeline.settings import get_settings
from bareun_pipeline.observability.logging import setup_logging, get_logger
from bareun_pipeline.domain.record import CertRecord
from bareun_pipeline.silver.merge import Merger

logger = get_logger(__name__)


def load_sample_records() -> List[CertRecord]:
    """
    Silver pre_merge NDJSON에서 CertRecord 로드
    
    E2E 테스트에서 저장한 Silver 정규화 데이터를 사용합니다.
    이 데이터에는 TouristInfo, GeneralRestaurant 등 모든 소스가 포함되어 있습니다.
    """
    import json

    settings = get_settings()

    # Silver 디렉토리에서 pre_merge 파일 찾기
    if not os.path.exists(settings.silver_dir):
        raise FileNotFoundError(
            f'Silver 디렉토리가 없습니다: {settings.silver_dir}\n'
            '먼저 E2E 테스트를 실행하세요: python scripts/e2e_sample_test.py'
        )

    silver_files = sorted([
        f for f in os.listdir(settings.silver_dir)
        if f.startswith('pre_merge_') and f.endswith('.ndjson')
    ], reverse=True)

    if not silver_files:
        raise FileNotFoundError(
            'Silver pre_merge 파일을 찾을 수 없습니다.\n'
            '먼저 E2E 테스트를 실행하세요: python scripts/e2e_sample_test.py'
        )

    latest_file = os.path.join(settings.silver_dir, silver_files[0])
    print(f'📂 로드 파일: {latest_file}')

    records = []
    errors = 0

    with open(latest_file, encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                record = CertRecord.model_validate(data)
                records.append(record)
            except Exception as e:
                errors += 1
                if errors <= 5:
                    logger.warning(
                        'record_load_error',
                        line=line_num,
                        error=str(e)[:200],
                    )

    print(f'✅ 로드 완료: {len(records)}건 (에러: {errors}건)')

    # 소스별 분포 출력
    source_counts: dict = {}
    for r in records:
        source_counts[r.source] = source_counts.get(r.source, 0) + 1

    print(f'   소스별 분포:')
    for source, count in sorted(source_counts.items()):
        print(f'     - {source}: {count}건')

    return records


def test_radius(records: List[CertRecord], radius_m: int) -> dict:
    """특정 반경으로 매칭 테스트"""
    print(f'\n{"="*70}')
    print(f'🧪 테스트 반경: {radius_m}m')
    print(f'{"="*70}')

    # 원본 레코드 깊은 복사 (각 테스트마다 독립적)
    import copy
    test_records = copy.deepcopy(records)

    merger = Merger(matching_radius_m=radius_m)
    merged = merger.merge(test_records)

    # 통계 계산
    tourist_count = sum(1 for r in merged if r.source == 'tourist_info')
    general_count = sum(1 for r in merged if r.source == 'general_restaurant')
    primary_count = sum(
        1 for r in merged
        if r.source in ('safe_restaurant', 'low_sodium', 'model_restaurant', 'food_safety')
    )

    with_menus = sum(1 for r in merged if r.main_menu)
    with_photos = sum(
        1 for r in merged
        if r.raw and isinstance(r.raw, dict) and r.raw.get('tourist_photos')
    )
    with_hours = sum(
        1 for r in merged
        if r.raw and isinstance(r.raw, dict) and r.raw.get('tourist_business_hours')
    )

    print(f'\n📊 결과:')
    print(f'   입력: {len(records)}건 → 출력: {len(merged)}건')
    print(f'   Primary (배지): {primary_count}건')
    print(f'   TouristInfo (잔존): {tourist_count}건')
    print(f'   GeneralRestaurant (잔존): {general_count}건')
    print(f'   메뉴 보강: {with_menus}건')
    print(f'   사진 보강: {with_photos}건')
    print(f'   영업시간 보강: {with_hours}건')

    return {
        'radius_m': radius_m,
        'input_count': len(records),
        'output_count': len(merged),
        'primary_count': primary_count,
        'tourist_count': tourist_count,
        'general_count': general_count,
        'with_menus': with_menus,
        'with_photos': with_photos,
        'with_hours': with_hours,
    }


def main():
    setup_logging(level=20, json_output=False)

    print('=' * 70)
    print('🔬 TouristInfo 매칭 반경 테스트')
    print('=' * 70)

    # Silver 데이터 로드
    records = load_sample_records()

    if not records:
        print('\n❌ 로드된 레코드가 없습니다.')
        return

    # 여러 반경 테스트
    radii = [100, 200, 300, 500, 1000]
    results = []

    for radius in radii:
        result = test_radius(records, radius)
        results.append(result)

    # 결과 비교 테이블
    print(f'\n{"="*70}')
    print('📊 반경별 결과 비교')
    print(f'{"="*70}')
    print(f'{"반경":>8} | {"출력":>6} | {"배지":>6} | {"Tourist":>8} | {"메뉴":>6} | {"사진":>6} | {"시간":>6}')
    print('-' * 70)

    for r in results:
        print(
            f"{r['radius_m']:>7}m | "
            f"{r['output_count']:>6} | "
            f"{r['primary_count']:>6} | "
            f"{r['tourist_count']:>8} | "
            f"{r['with_menus']:>6} | "
            f"{r['with_photos']:>6} | "
            f"{r['with_hours']:>6}"
        )

    # 분석
    print(f'\n{"="*70}')
    print('💡 분석')
    print(f'{"="*70}')

    # TouristInfo 잔존이 가장 적은 반경
    min_tourist = min(results, key=lambda x: x['tourist_count'])
    print(
        f'✅ TouristInfo 잔존 최소화: {min_tourist["radius_m"]}m '
        f'(잔존: {min_tourist["tourist_count"]}건)'
    )

    # 메뉴 보강이 가장 많은 반경
    max_menus = max(results, key=lambda x: x['with_menus'])
    print(
        f'✅ 메뉴 보강 최대화: {max_menus["radius_m"]}m '
        f'(메뉴: {max_menus["with_menus"]}건)'
    )

    # 사진 보강이 가장 많은 반경
    max_photos = max(results, key=lambda x: x['with_photos'])
    print(
        f'✅ 사진 보강 최대화: {max_photos["radius_m"]}m '
        f'(사진: {max_photos["with_photos"]}건)'
    )

    print(f'\n🎯 추천: 메뉴/사진 보강이 충분하면서 TouristInfo 잔존이 적은 반경 선택')


if __name__ == '__main__':
    main()
    