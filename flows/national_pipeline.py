# flows/national_pipeline.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from prefect import flow, task
from pipeline import run_pipeline, PROVINCES

@task(retries=3, retry_delay_seconds=15)
def collect_one(province):
    run_pipeline(province=province, output=f'out/{province}.csv')

@task
def ingest_all():
    import ingest_to_supabase
    ingest_to_supabase.main()

@flow(name="bareun-national-pipeline")
def national_flow():
    for p in PROVINCES:
        collect_one(p)
    ingest_all()

if __name__ == '__main__':
    # 1회 실행
    #national_flow()
    # 매일 새벽 4시(KST) 자동 실행
     national_flow.serve(cron="0 19 * * *", timezone="UTC")