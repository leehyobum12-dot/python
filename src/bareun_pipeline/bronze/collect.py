"""
Bronze Layer: 수집 Orchestration
==================================
- 어댑터 병렬 수집 (ThreadPoolExecutor)
- 시도별 체크포인트 관리
- 구조화 로깅 (run_id, province 컨텍스트)
- 원본 데이터 보존 (RawArchiver 연동)

성능 개선:
- 어댑터 병렬 실행으로 수집 속도 2~3배 향상
- 각 어댑터는 I/O 바운드 작업이므로 병렬화 효과 큼
"""
from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import List, Dict, Any, Optional, TypedDict

from ..observability.logging import get_logger
from ..observability.quota import QuotaManager
from ..io.checkpoint import CheckpointManager
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
    adapters: Dict[str, AdapterResult]
    total_records: int
    skipped: bool


class NationwideResult(TypedDict):
    """전국 수집 결과"""
    run_id: str
    provinces: Dict[str, ProvinceResult]
    total_records: int
    completed: int
    skipped: int
    failed: int


class BronzeCollector:
    """
    Bronze Layer 수집기

    사용법:
        collector = BronzeCollector()
        results = collector.collect_province('제주특별자치도')
        results = collector.collect_nationwide()

    성능:
        어댑터 병렬 실행으로 수집 속도 2~3배 향상
    """

    # 어댑터 병렬 실행 시 최대 워커 수
    MAX_ADAPTER_WORKERS = 4

    def __init__(self):
        self.settings = get_settings()
        self.checkpoint = CheckpointManager(self.settings.checkpoint_dir)
        self.archiver = RawArchiver()
        self.quota = QuotaManager.get_instance()

        # 어댑터 클래스 목록 (지연 import)
        self._adapter_classes: Optional[List[type]] = None

    def _get_adapter_classes(self) -> List[type]:
        """어댑터 클래스 목록 반환 (지연 import)"""
        if self._adapter_classes is None:
            from ..adapters.safe_restaurant import SafeRestaurantAdapter
            from ..adapters.low_sodium import LowSodiumAdapter
            from ..adapters.food_safety import FoodSafetyAdapter
            from ..adapters.model_restaurant import ModelRestaurantAdapter

            self._adapter_classes = [
                SafeRestaurantAdapter,
                LowSodiumAdapter,
                FoodSafetyAdapter,
                ModelRestaurantAdapter,
            ]

        return self._adapter_classes

    def _collect_one_adapter(
        self,
        AdapterClass: type,
        province: str,
        batch_id: str,
    ) -> AdapterResult:
        """
        단일 어댑터 수집 (워커 함수)

        Args:
            AdapterClass: 어댑터 클래스
            province: 시도명
            batch_id: 배치 식별자

        Returns:
            어댑터별 수집 결과
        """
        adapter_name = AdapterClass.__name__

        try:
            adapter = AdapterClass()
            records = adapter.collect(province=province)

            # 원본 데이터 보존
            if adapter.raw_rows:
                self.archiver.archive(
                    source=adapter.source,
                    province=province,
                    rows=adapter.raw_rows,
                    batch_id=batch_id,
                )

            return {
                'records': len(records),
                'raw_rows': len(adapter.raw_rows),
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
            )
            return {
                'records': 0,
                'raw_rows': 0,
                'status': 'failed',
                'error': str(e),
            }

    def collect_province(
        self,
        province: str,
        force: bool = False,
    ) -> ProvinceResult:
        """
        단일 시도 수집 (어댑터 병렬 실행)

        Args:
            province: 시도명
            force: True이면 체크포인트 무시하고 강제 수집

        Returns:
            수집 결과 요약
        """
        run_id = str(uuid.uuid4())[:8]
        batch_id = datetime.now().strftime('%Y%m%d_%H%M%S')

        # 로그 컨텍스트 바인딩
        log = logger.bind(
            run_id=run_id,
            province=province,
            batch_id=batch_id,
        )

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
            province,
            max_age_hours=self.settings.checkpoint_max_age_hours,
        ):
            log.info('collect_province_skip_checkpoint')
            result['skipped'] = True
            return result

        # 어댑터 병렬 수집
        adapter_classes = self._get_adapter_classes()

        with ThreadPoolExecutor(max_workers=self.MAX_ADAPTER_WORKERS) as executor:
            futures = {
                executor.submit(
                    self._collect_one_adapter,
                    AdapterClass,
                    province,
                    batch_id,
                ): AdapterClass.__name__
                for AdapterClass in adapter_classes
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
                    log.error(
                        'adapter_unexpected_error',
                        adapter=adapter_name,
                        error=str(e),
                    )
                    result['adapters'][adapter_name] = {
                        'records': 0,
                        'raw_rows': 0,
                        'status': 'failed',
                        'error': str(e),
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
        provinces: Optional[List[str]] = None,
        force: bool = False,
    ) -> NationwideResult:
        """
        전국 시도 순회 수집

        Args:
            provinces: 수집 대상 시도 목록 (None이면 설정값 사용)
            force: True이면 체크포인트 무시

        Returns:
            전국 수집 결과 요약
        """
        if provinces is None:
            provinces = self.settings.provinces

        run_id = str(uuid.uuid4())[:8]
        log = logger.bind(run_id=run_id)

        log.info(
            'collect_nationwide_start',
            provinces=provinces,
            count=len(provinces),
        )

        results: NationwideResult = {
            'run_id': run_id,
            'provinces': {},
            'total_records': 0,
            'completed': 0,
            'skipped': 0,
            'failed': 0,
        }

        for i, province in enumerate(provinces, 1):
            log.info(
                'collect_nationwide_progress',
                current=i,
                total=len(provinces),
                province=province,
            )

            try:
                province_result = self.collect_province(province, force=force)
                results['provinces'][province] = province_result

                if province_result['skipped']:
                    results['skipped'] += 1
                else:
                    results['completed'] += 1
                    results['total_records'] += province_result['total_records']

            except Exception as e:
                log.error(
                    'collect_province_unexpected_error',
                    province=province,
                    error=str(e),
                    error_type=type(e).__name__,
                )
                error_result: ProvinceResult = {
                    'province': province,
                    'run_id': run_id,
                    'batch_id': '',
                    'adapters': {},
                    'total_records': 0,
                    'skipped': False,
                }
                results['provinces'][province] = error_result
                results['failed'] += 1

        # 쿼터 최종 저장
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