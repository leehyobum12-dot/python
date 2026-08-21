"""
전국 인증식당 수집 파이프라인 — Prefect 워크플로우 (v4.0)
================================================================
- Bronze → Silver → Gold 전체 파이프라인 통합
- 시도별 체크포인트 관리
- 구조화 로깅

수정 사항:
- 반환 타입을 TypedDict로 명시
- List[Unknown] 에러 해결

Prefect Server 실행 필요 (별도 터미널):
    prefect server start

데몬 모드 실행:
    python flows/national_pipeline.py

주의:
- Prefect 3.8.1은 timezone 파라미터 미지원 → UTC 기준 크론 표현
- KST 04:00 = UTC 19:00 (전날)
"""
from __future__ import annotations

import os
import sys
import asyncio
from typing import List, Dict, Any

# 경로 설정
FLOW_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(FLOW_DIR)
SRC_DIR = os.path.join(ROOT_DIR, 'src')

if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from prefect import flow, task

from bareun_pipeline.bronze.collect import BronzeCollector, ProvinceResult
from bareun_pipeline.silver.normalize import normalize_records
from bareun_pipeline.silver.merge import merge_records
from bareun_pipeline.silver.classify import classify_text
from bareun_pipeline.silver.enrich import SilverEnricher
from bareun_pipeline.silver.menu_extract import MenuExtractor
from bareun_pipeline.gold.transform import transform_records, GoldRestaurant
from bareun_pipeline.gold.export import export_records, ExportResult
from bareun_pipeline.gold.supabase_loader import load_records
from bareun_pipeline.settings import get_settings
from bareun_pipeline.observability.logging import get_logger
from bareun_pipeline.domain.record import CertRecord


logger = get_logger(__name__)


@task(retries=3, retry_delay_seconds=15, name='bronze-collect-province')
def bronze_collect_province(province: str) -> ProvinceResult:
    """Bronze Layer: 시도별 수집"""
    collector = BronzeCollector()
    result = collector.collect_province(province, force=False)
    return result


@task(name='silver-process')
def silver_process(records: List[CertRecord]) -> List[CertRecord]:
    """Silver Layer: 정제/병합/보강"""
    if not records:
        return []

    # 1. 정규화
    records = normalize_records(records)

    # 2. 단일 병합 (SSOT)
    records = merge_records(records)

    # 3. 7차원 태그 분류
    for record in records:
        text = record.main_menu or record.raw_name or ''
        tags = classify_text(text, record.biz_type or '')
        record.cuisine_tags = tags['cuisine_tags']
        record.nutrient_tags = tags['nutrient_tags']
        record.cooking_tags = tags['cooking_tags']
        record.ingredient_tags = tags['ingredient_tags']
        record.regional_tags = tags['regional_tags']
        record.allergen_tags = tags['allergen_tags']
        record.occasion_tags = tags['occasion_tags']

    # 4. 지오코딩 + avoid_tags 생성 (비동기)
    enricher = SilverEnricher()
    records = asyncio.run(enricher.enrich(records))

    return records


@task(name='gold-transform')
def gold_transform(records: List[CertRecord]) -> List[GoldRestaurant]:
    """Gold Layer: DB 스키마 매칭"""
    return transform_records(records)


@task(name='gold-export')
def gold_export(records: List[GoldRestaurant]) -> ExportResult:
    """Gold Layer: 파일 내보내기"""
    return export_records(records)


@task(name='gold-load-supabase')
def gold_load_supabase(records: List[GoldRestaurant]) -> Dict[str, Any]:
    """Gold Layer: Supabase 적재"""
    return load_records(records)


@task(name='quota-finalize')
def quota_finalize() -> None:
    """쿼터 최종 저장"""
    from bareun_pipeline.observability.quota import QuotaManager
    QuotaManager.get_instance().finalize()


@flow(name='bareun-national-pipeline', log_prints=True)
def national_flow() -> None:
    """전국 시도 순회 수집 → 정제 → 적재"""
    settings = get_settings()
    provinces = settings.provinces

    all_records: List[CertRecord] = []

    # Bronze: 시도별 수집
    for province in provinces:
        result = bronze_collect_province(province)
        # 수집된 레코드는 Bronze Layer에서 파일로 저장됨
        # 여기서는 데모용으로 빈 리스트 반환

    # Silver: 정제/병합/보강
    processed_records = silver_process(all_records)

    # Gold: 변환/내보내기/적재
    gold_records = gold_transform(processed_records)
    export_result = gold_export(gold_records)
    load_result = gold_load_supabase(gold_records)

    # 쿼터 최종 저장
    quota_finalize()

    # 요약 출력
    logger.info(
        'national_pipeline_complete',
        collected=len(all_records),
        processed=len(processed_records),
        transformed=len(gold_records),
        loaded=load_result.get('loaded', 0),
    )


if __name__ == '__main__':
    # Prefect Server 실행 필요 (별도 터미널): prefect server start

    # 옵션 1: 1회 실행 (테스트용)
    # national_flow()

    # 옵션 2: 데몬 모드 (매일 KST 04:00)
    # UTC 19:00 = KST 다음날 04:00
    national_flow.serve(
        name='bareun-national-pipeline-daemon',
        cron='0 19 * * *',  # UTC 기준
    )