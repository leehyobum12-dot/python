# flows/national_pipeline.py
"""전국 인증식당 수집 파이프라인 — Prefect 워크플로우 (v3.8.1 호환)"""

import sys
import os

# ─────────────── 경로 설정 ───────────────
FLOW_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(FLOW_DIR)           # python/ (git root)
PIPELINE_DIR = os.path.join(ROOT_DIR, 'data_pipeline')

if PIPELINE_DIR not in sys.path:
    sys.path.insert(0, PIPELINE_DIR)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

# ─────────────── Import ───────────────
from prefect import flow, task
from pipeline import run_pipeline, PROVINCES, OUT_DIR


# ─────────────── Task 정의 ───────────────
@task(retries=3, retry_delay_seconds=15, name="collect-one-province")
def collect_one(province: str) -> None:
    """시도별 수집 (Prefect 재시도 활용)"""
    output = os.path.join(OUT_DIR, f"{province}.csv")
    run_pipeline(province=province, output=output)


@task(name="ingest-all-to-supabase")
def ingest_all() -> None:
    """전국 CSV를 Supabase에 통합 적재"""
    import ingest_to_supabase
    ingest_to_supabase.main()


# ─────────────── Flow 정의 ───────────────
@flow(name="bareun-national-pipeline", log_prints=True)
def national_flow() -> None:
    """전국 16개 시도 순회 수집 → Supabase 적재"""
    for p in PROVINCES:
        collect_one(p)
    ingest_all()


# ─────────────── 진입점 ───────────────
if __name__ == '__main__':
    # ⚠️ Prefect Server 실행 필요 (별도 터미널에서)
    #   prefect server start

    # 옵션 1: 1회 실행 (테스트용)
    # national_flow()

    # 옵션 2: 데몬 모드
    # ✅ KST 04:00 = UTC 19:00 (전날)
    # ⚠️ Prefect 3.8.1은 timezone 파라미터 미지원 → UTC 기준으로 직접 지정
    national_flow.serve(
        name="bareun-national-pipeline-daemon",
        cron="0 19 * * *",   # UTC 19:00 = KST 다음날 04:00
    )