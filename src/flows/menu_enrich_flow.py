"""
메뉴 Enrichment 파이프라인 — Prefect 워크플로우 (v4.0)
========================================================
- 매일 새벽 1시 실행 (쿼터 리셋 후)
- menu_missing 레코드만 NER+LLM enrich

Prefect Server 실행 필요 (별도 터미널):
    prefect server start

데몬 모드 실행:
    python flows/menu_enrich_flow.py

주의:
- Prefect 3.8.1은 timezone 파라미터 미지원 → UTC 기준 크론 표현
- KST 01:00 = UTC 16:00 (전날)
"""
from __future__ import annotations

import os
import sys
from typing import Dict, Any, TypedDict

# 경로 설정
FLOW_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(FLOW_DIR)
SRC_DIR = os.path.join(ROOT_DIR, 'src')

if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from prefect import flow, task

from bareun_pipeline.observability.logging import get_logger


logger = get_logger(__name__)


class EnrichResult(TypedDict):
    """Enrichment 결과"""
    status: str
    processed: int
    errors: int


@task(name='run-menu-enrichment')
def run_enrichment_task() -> EnrichResult:
    """메뉴 enrichment 실행"""
    # Phase 4에서 구현될 Silver Layer의 menu_extract 호출
    # 현재는 placeholder
    logger.info('menu_enrichment_placeholder')
    return {'status': 'placeholder', 'processed': 0, 'errors': 0}


@flow(name='menu-enrich-daily', log_prints=True)
def menu_enrich_flow() -> None:
    """매일 쿼터 리셋 후 이어서 enrichment"""
    result = run_enrichment_task()
    logger.info('menu_enrichment_complete', result=result)


if __name__ == '__main__':
    # 매일 새벽 1시 (KST)
    # UTC 16:00 = KST 다음날 01:00
    menu_enrich_flow.serve(
        name='menu-enrich-daily-daemon',
        cron='0 16 * * *',  # UTC 기준
    )