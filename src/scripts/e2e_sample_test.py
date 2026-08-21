#!/usr/bin/env python3
"""
E2E 샘플 테스트 스크립트 (v2.1)
==================================
Option A: 좌표 보강 (네이버 지오코딩)
Option B: 가게 이름 검증 (네이버 + 카카오 맵 API)
Option C: 메뉴 보강 (NER + LLM)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
import time
from datetime import datetime
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

logger = get_logger(__name__)


def sample_records(
    records: List[CertRecord],
    sample_size: int = 100,
    seed: int = 42,
) -> List[CertRecord]:
    """랜덤 샘플링 (seed 고정으로 재현성 보장)"""
    if len(records) <= sample_size:
        return records
    random.seed(seed)
    return random.sample(records, sample_size)


async def enrich_data(
    records: List[CertRecord],
    enable_geocoding: bool = True,
    enable_store_verification: bool = True,
) -> List[CertRecord]:
    """
    Silver Layer 통합 보강

    Args:
        records: 보강할 레코드 목록
        enable_geocoding: 좌표 보강 활성화
        enable_store_verification: 가게 이름 검증 활성화

    Returns:
        보강된 레코드 목록
    """
    from bareun_pipeline.silver.enrich import SilverEnricher

    enricher = SilverEnricher()

    if enable_geocoding:
        missing_coords = [r for r in records if not r.has_coordinate()]
        if not missing_coords:
            logger.info('enrich_coordinates_skip_all_have_coords')
        else:
            logger.info(
                'enrich_coordinates_start',
                missing=len(missing_coords),
                total=len(records),
            )

    # 통합 보강 실행 (지오코딩 + 가게 이름 검증)
    enriched = await enricher.enrich(
        records,
        verify_store_names=enable_store_verification,
    )

    # 결과 로깅
    if enable_geocoding:
        success_count = sum(1 for r in enriched if r.has_coordinate())
        logger.info(
            'enrich_coordinates_complete',
            success=success_count,
            total=len(records),
        )

    if enable_store_verification:
        verified_count = sum(1 for r in enriched if r.store_name_quality == 'verified')
        needs_verify_count = sum(1 for r in enriched if r.store_name_quality == 'needs_verify')
        logger.info(
            'enrich_store_verification_complete',
            verified=verified_count,
            needs_verify=needs_verify_count,
        )

    return enriched


def run_e2e_test(
    province: str = '제주특별자치도',
    sample_size: int = 100,
    seed: int = 42,
    enable_geocoding: bool = True,
    enable_store_verification: bool = True,
    skip_food_safety: bool = False,
) -> None:
    """E2E 테스트 실행 (v2.1)"""
    settings = get_settings()
    setup_logging(level=20, json_output=False)

    print('=' * 70)
    print(f'🧪 E2E 샘플 테스트 v2.1: {province}')
    print(f'   샘플 크기: {sample_size}, 시드: {seed}')
    print(f'   좌표 보강: {"✅ 활성화" if enable_geocoding else "❌ 비활성화"}')
    print(f'   가게 검증: {"✅ 활성화" if enable_store_verification else "❌ 비활성화"}')
    print('=' * 70)

    start_time = time.time()
    settings.ensure_directories()

    # Step 1: 각 어댑터를 직접 호출하여 CertRecord 수집
    print('\n📥 Step 1: 데이터 수집 중...')
    from bareun_pipeline.adapters.safe_restaurant import SafeRestaurantAdapter
    from bareun_pipeline.adapters.low_sodium import LowSodiumAdapter
    from bareun_pipeline.adapters.food_safety import FoodSafetyAdapter
    from bareun_pipeline.adapters.model_restaurant import ModelRestaurantAdapter
    from bareun_pipeline.bronze.raw_archiver import RawArchiver

    archiver = RawArchiver()
    batch_id = datetime.now().strftime('%Y%m%d_%H%M%S')
    all_records: List[CertRecord] = []

    # 어댑터 목록 (FoodSafety 선택적 제외)
    adapters = [
        ('SafeRestaurant', SafeRestaurantAdapter()),
        ('LowSodium', LowSodiumAdapter()),
        ('ModelRestaurant', ModelRestaurantAdapter()),
    ]

    if not skip_food_safety:
        adapters.append(('FoodSafety', FoodSafetyAdapter()))

    for name, adapter in adapters:
        try:
            print(f'   [{name}] 수집 중...')
            records = adapter.collect(province=province)
            print(f'   [{name}] 수집 완료: {len(records)}건')
            all_records.extend(records)

            # 원본 보존
            if adapter.raw_rows:
                archiver.archive(
                    source=adapter.source,
                    province=province,
                    rows=adapter.raw_rows,
                    batch_id=batch_id,
                )

        except Exception as e:
            print(f'   [{name}] 실패: {type(e).__name__}: {e}')
            import traceback
            traceback.print_exc()

    print(f'\n   총 수집: {len(all_records)}건')

    if not all_records:
        print('\n❌ 수집된 레코드가 없습니다. 테스트를 중단합니다.')
        return

    # Step 2: 랜덤 샘플링
    print(f'\n🎲 Step 2: 랜덤 {sample_size}개 샘플링 중...')
    sampled_records = sample_records(all_records, sample_size, seed)
    print(f'   샘플링 완료: {len(sampled_records)}건')

    # Step 3: Silver - 정규화
    print('\n🔧 Step 3: Silver - 정규화 중...')
    from bareun_pipeline.silver.normalize import normalize_records
    normalized = normalize_records(sampled_records)
    print(f'   정규화 완료: {len(normalized)}건')

    # Step 4: Silver - 병합
    print('\n🔀 Step 4: Silver - 병합 중...')
    from bareun_pipeline.silver.merge import merge_records
    merged = merge_records(normalized)
    print(f'   병합 완료: {len(merged)}건 (중복 제거)')

    # Step 5: Silver - 통합 보강 (지오코딩 + 가게 이름 검증)
    print('\n📍 Step 5: Silver - 통합 보강 중...')
    before_coords = sum(1 for r in merged if r.has_coordinate())
    
    merged = asyncio.run(enrich_data(
        merged,
        enable_geocoding=enable_geocoding,
        enable_store_verification=enable_store_verification,
    ))
    
    after_coords = sum(1 for r in merged if r.has_coordinate())
    verified_count = sum(1 for r in merged if r.store_name_quality == 'verified')
    
    print(f'   좌표 보강: {before_coords}건 → {after_coords}건')
    print(f'   가게 검증: {verified_count}건 업데이트됨')

    # Step 6: Silver - 7차원 태그 분류
    print('\n🏷️  Step 6: Silver - 7차원 태그 분류 중...')
    from bareun_pipeline.silver.classify import classify_text
    from bareun_pipeline.domain.avoid_tags import generate_avoid_tags

    for record in merged:
        text = record.main_menu or record.raw_name or ''
        if text:
            tags = classify_text(text, record.biz_type or '')
            record.cuisine_tags = tags['cuisine_tags']
            record.nutrient_tags = tags['nutrient_tags']
            record.cooking_tags = tags['cooking_tags']
            record.ingredient_tags = tags['ingredient_tags']
            record.regional_tags = tags['regional_tags']
            record.allergen_tags = tags['allergen_tags']
            record.occasion_tags = tags['occasion_tags']
            
            # avoid_tags 생성 (알레르기 기반, 올바른 시그니처)
            record.avoid_tags = generate_avoid_tags(
                allergen_tags=record.allergen_tags,
                biz_type=record.biz_type,
                main_menu=record.main_menu,
                menus=record.menus,
            )

    print(f'   분류 완료: {len(merged)}건')

    # Step 7: Gold - 변환
    print('\n💾 Step 7: Gold - 변환 중...')
    from bareun_pipeline.gold.transform import transform_records
    gold_records = transform_records(merged)
    print(f'   변환 완료: {len(gold_records)}건')

    # Step 8: Gold - CSV + NDJSON 내보내기
    print('\n📊 Step 8: Gold - 파일 내보내기 중...')
    from bareun_pipeline.gold.export import GoldExporter
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    exporter = GoldExporter(gold_dir=settings.gold_dir)
    result = exporter.export_dual(gold_records, filename_base=f'e2e_test_{timestamp}')
    print(f'   NDJSON: {result["ndjson_path"]}')
    print(f'   CSV: {result["csv_path"]}')

    # Step 9: CSV 품질 검증
    print('\n🔍 Step 9: CSV 품질 검증 중...')
    import pandas as pd
    df = pd.read_csv(result['csv_path'], encoding='utf-8-sig')
    print(f'   총 레코드: {len(df)}건')
    print(f'   메뉴 발견: {df["main_menu"].notna().sum()}건')
    print(f'   좌표 발견: {df["lat"].notna().sum()}건')

    # 품질 점수 분포
    if 'quality_score' in df.columns:
        print('\n   품질 점수 분포:')
        print(f'   - 90점 이상: {(df["quality_score"] >= 90).sum()}건')
        print(f'   - 70~89점: {((df["quality_score"] >= 70) & (df["quality_score"] < 90)).sum()}건')
        print(f'   - 50~69점: {((df["quality_score"] >= 50) & (df["quality_score"] < 70)).sum()}건')
        print(f'   - 50점 미만: {(df["quality_score"] < 50).sum()}건')

    # 결과 요약
    elapsed = time.time() - start_time
    print('\n' + '=' * 70)
    print(f'🎉 E2E 테스트 완료! (소요: {elapsed:.1f}초)')
    print(f'   수집: {len(all_records)}건')
    print(f'   샘플: {len(sampled_records)}건')
    print(f'   병합: {len(merged)}건')
    print(f'   Gold: {len(gold_records)}건')
    print(f'   CSV 파일: {result["csv_path"]}')
    print('=' * 70)

    print('\n📋 다음 단계: 상세 품질 검증')
    print(f'   python scripts/verify_data_quality.py {result["csv_path"]}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='E2E 샘플 테스트 v2.1')
    parser.add_argument('--province', default='제주특별자치도', help='수집 대상 시도')
    parser.add_argument('--sample-size', type=int, default=100, help='샘플 크기')
    parser.add_argument('--seed', type=int, default=42, help='랜덤 시드')
    parser.add_argument('--no-geocoding', action='store_true', help='좌표 보강 비활성화')
    parser.add_argument('--no-store-verification', action='store_true', help='가게 이름 검증 비활성화')
    parser.add_argument('--skip-food-safety', action='store_true', help='FoodSafety 어댑터 건너뛰기')
    args = parser.parse_args()

    run_e2e_test(
        province=args.province,
        sample_size=args.sample_size,
        seed=args.seed,
        enable_geocoding=not args.no_geocoding,
        enable_store_verification=not args.no_store_verification,
        skip_food_safety=args.skip_food_safety,
    )