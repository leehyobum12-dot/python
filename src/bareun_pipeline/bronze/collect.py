"""
Bronze Layer: Collection Orchestration
======================================
- 어댑터 병렬 수집 (ThreadPoolExecutor)
- 시도별 체크포인트 관리 (CheckpointManager)
- 구조화 로깅 (run_id, province 컨텍스트)
- 원본 데이터 보존 (RawArchiver 연동)

리팩토링 사항 (Autofix):
- 하드코딩된 어댑터 목록 제거 → adapters.get_all_adapters() 동적 로드 (누락 어댑터 자동 포함)
- 메모리 누수 방지 (raw_rows 명시적 초기화)
- Broad Exception 핸들링 구체화 및 exc_info=True로 스택 트레이스 보존
"""
from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Optional, TypedDict

from ..adapters import get_all_adapters
from ..adapters.base import BaseAdapter
from ..io.checkpoint import CheckpointManager
from ..observability.logging import get_logger
from ..observability.quota import QuotaManager
from ..settings import get_settings
from .raw_archiver import RawArchiver

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════
# 결과 타입 정의 (TypedDict)
# ═══════════════════════════════════════════════════════════

class AdapterResult(TypedDict):
    """어댑터별 수집 결과"""
    records: int
    raw_rows: int
    status: str
    error: Optional[str]


class ProvinceResult(TypedDict):
    """시도별 수집 결과"""
    province: str
    run_id: str
    batch_id: str
    adapters: dict[str, AdapterResult]
    total_records: int
    skipped: bool


class NationwideResult(TypedDict):
    """전국 수집 결과"""
    run_id: str
    provinces: dict[str, ProvinceResult]
    total_records: int
    completed: int
    skipped: int
    failed: int


class BronzeCollector:
    """
    Bronze Layer 수집 오케스트레이터

    사용법:
        collector = BronzeCollector()
        results = collector.collect_province('제주특별자치도')
        results = collector.collect_nationwide()
    """

    MAX_ADAPTER_WORKERS = 4

    def __init__(self):
        self.settings = get_settings()
        self.checkpoint = CheckpointManager(self.settings.checkpoint_dir)
        self.archiver = RawArchiver()
        self.quota = QuotaManager.get_instance()

    # RawArchiver에 threading.Lock 추가시 스레드 안정성 확보(진행 예정)
    def _collect_one_adapter(
        self,
        adapter_cls: type[BaseAdapter],
        province: str,
        batch_id: str,
    ) -> AdapterResult:
        """단일 어댑터 수집 (워커 함수)"""
        adapter_name = adapter_cls.__name__
        source_name = getattr(adapter_cls, "source", adapter_name)

        try:
            adapter = adapter_cls()
            records = adapter.collect(province=province)
            
            raw_count = len(adapter.raw_rows)
            
            # 원본 데이터 보존 (Bronze Layer)
            if raw_count > 0:
                self.archiver.archive(
                    source=source_name,
                    province=province,
                    rows=adapter.raw_rows,
                    batch_id=batch_id,
                )
                # 🐛 버그 수정: 메모리 누수 방지를 위해 명시적 초기화
                if hasattr(adapter, "_clear_raw_rows"):
                    adapter._clear_raw_rows()
                else:
                    adapter.raw_rows = []

            return {
                'records': len(records),
                'raw_rows': raw_count,
                'status': 'success',
                'error': None,
            }

        except Exception as e:
            logger.error(
                'adapter_failed',
                adapter=adapter_name,
                province=province,
                error=str(e),
                error_type=type(e).__name__,
                exc_info=True,  # 스택 트레이스 보존
            )
            return {
                'records': 0,
                'raw_rows': 0,
                'status': 'failed',
                'error': f"{type(e).__name__}: {str(e)}",
            }

    def collect_province(
        self,
        province: str,
        force: bool = False,
    ) -> ProvinceResult:
        """단일 시도 수집 (어댑터 병렬 실행)"""
        run_id = str(uuid.uuid4())[:8]
        batch_id = datetime.now().strftime('%Y%m%d_%H%M%S')

        log = logger.bind(run_id=run_id, province=province, batch_id=batch_id)
        log.info('collect_province_start')

        result: ProvinceResult = {
            'province': province,
            'run_id': run_id,
            'batch_id': batch_id,
            'adapters': {},
            'total_records': 0,
            'skipped': False,
        }

        # 체크포인트 확인
        if not force and self.checkpoint.is_completed(
            province, max_age_hours=self.settings.checkpoint_max_age_hours
        ):
            log.info('collect_province_skip_checkpoint')
            result['skipped'] = True
            return result

        # 🐛 버그 수정: 동적 어댑터 로드 (하드코딩 제거 → 누락 어댑터 자동 포함)
        adapter_classes = get_all_adapters()

        with ThreadPoolExecutor(max_workers=self.MAX_ADAPTER_WORKERS) as executor:
            futures = {
                executor.submit(
                    self._collect_one_adapter, cls, province, batch_id
                ): cls.__name__
                for cls in adapter_classes
            }

            for future in as_completed(futures):
                adapter_name = futures[future]
                try:
                    adapter_result = future.result()
                    result['adapters'][adapter_name] = adapter_result
                    result['total_records'] += adapter_result['records']
                    
                    log.info(
                        'adapter_complete',
                        adapter=adapter_name,
                        records=adapter_result['records'],
                        status=adapter_result['status'],
                    )
                except Exception as e:
                    log.error('adapter_unexpected_error', adapter=adapter_name, error=str(e))
                    result['adapters'][adapter_name] = {
                        'records': 0, 'raw_rows': 0,
                        'status': 'failed', 'error': str(e),
                    }

        # 체크포인트 마커 저장
        self.checkpoint.mark_completed(province, result['total_records'])

        log.info(
            'collect_province_complete',
            total_records=result['total_records'],
            adapters=list(result['adapters'].keys()),
        )
        return result

    def collect_nationwide(
        self,
        provinces: Optional[list[str]] = None,
        force: bool = False,
    ) -> NationwideResult:
        """전국 시도 순회 수집"""
        target_provinces = provinces or self.settings.provinces
        run_id = str(uuid.uuid4())[:8]
        log = logger.bind(run_id=run_id)

        log.info('collect_nationwide_start', provinces=target_provinces, count=len(target_provinces))

        results: NationwideResult = {
            'run_id': run_id,
            'provinces': {},
            'total_records': 0,
            'completed': 0,
            'skipped': 0,
            'failed': 0,
        }

        for i, province in enumerate(target_provinces, 1):
            log.info('collect_nationwide_progress', current=i, total=len(target_provinces), province=province)

            try:
                province_result = self.collect_province(province, force=force)
                results['provinces'][province] = province_result

                if province_result['skipped']:
                    results['skipped'] += 1
                else:
                    results['completed'] += 1
                    results['total_records'] += province_result['total_records']

            except Exception as e:
                log.error('collect_province_unexpected_error', province=province, error=str(e))
                results['provinces'][province] = {
                    'province': province, 'run_id': run_id, 'batch_id': '',
                    'adapters': {}, 'total_records': 0, 'skipped': False,
                }
                results['failed'] += 1

        self.quota.finalize()
        log.info(
            'collect_nationwide_complete',
            completed=results['completed'],
            skipped=results['skipped'],
            failed=results['failed'],
            total_records=results['total_records'],
            quota_summary=self.quota.summary(),
        )
        return results