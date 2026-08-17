# flows/national_pipeline.py 또는 별도 menu_enrich_flow.py
from prefect import flow
from prefect.schedules import CronSchedule

@flow(name="menu-enrich-daily")
def menu_enrich_flow():
    """매일 쿼터 리셋 후 이어서 enrichment"""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'data_pipeline'))
    from menu_enrich_runner import run_enrichment
    run_enrichment()

if __name__ == '__main__':
    menu_enrich_flow.serve(
        name="menu-enrich-daily-daemon",
        cron="0 1 * * *",              # ✅ 매일 새벽 1시 (쿼터 리셋 후)
        timezone="Asia/Seoul",
    )