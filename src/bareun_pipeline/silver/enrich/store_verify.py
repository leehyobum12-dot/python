"""
Bareun Pipeline: Silver Enrichment - Store Name Verification
=============================================================
- 가게 이름 검증 오케스트레이션 (네이버 + 카카오)
- 캐시 관리 및 배치 처리
- I/O는 adapters/local_search_client.py에 위임

전제조건 (Precondition):
  - 이 모듈을 호출하기 전에 `record.store_name_quality` 필드가 채워져 있어야 합니다.
  - 초기 평가는 `silver/enrich/__init__.py`의 오케스트레이터가 `assess_store_name_quality`를 호출해 처리합니다.

이관 출처: silver/enrich.py (StoreNameVerifier)
수정 사항 (Autofix):
  - 미사용 임포트(`assess_store_name_quality`) 제거하여 Lint F401 경고 해소
"""

from __future__ import annotations

import asyncio
import json
import os
import threading

import httpx

from ...adapters.local_search_client import LocalSearchAdapter
from ...domain.avoid_tags import (
    build_search_query,
    needs_store_verification,
)
from ...domain.record import CertRecord
from ...observability.logging import get_logger
from ...settings import get_settings

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════
# 캐시 관리
# ═══════════════════════════════════════════════════════════


class StoreVerifyCache:
    """가게명 검증 캐시 (파일 기반)"""

    def __init__(self, cache_path: str):
        self._path = cache_path
        self._cache: dict[str, str | None] = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if os.path.exists(self._path):
            try:
                with open(self._path, encoding='utf-8') as f:
                    self._cache = json.load(f)
                logger.info('store_verify_cache_loaded', count=len(self._cache))
            except Exception:
                self._cache = {}

    def save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with self._lock:
                temp_path = self._path + '.tmp'
                with open(temp_path, 'w', encoding='utf-8') as f:
                    json.dump(self._cache, f, ensure_ascii=False)
                os.replace(temp_path, self._path)
        except Exception as e:
            logger.warning('store_verify_cache_save_failed', error=str(e))

    def get(self, query: str) -> str | None:
        with self._lock:
            return self._cache.get(query)

    def set(self, query: str, result: str | None) -> None:
        with self._lock:
            self._cache[query] = result


# ═══════════════════════════════════════════════════════════
# 검증 서비스 (어댑터 오케스트레이션)
# ═══════════════════════════════════════════════════════════


class StoreVerifyService:
    """가게 이름 검증 서비스"""

    def __init__(self):
        self.settings = get_settings()
        self.adapter = LocalSearchAdapter(settings=self.settings)
        self.cache = StoreVerifyCache(
            os.path.join(self.settings.cache_dir, 'store_verify', 'store_verify_cache.json')
        )

    async def verify_batch(
        self,
        records: list[CertRecord],
        max_concurrency: int = 3,
    ) -> list[CertRecord]:
        """배치 가게명 검증"""
        needs_verification = [
            r for r in records if needs_store_verification(r.store_name_quality or 'valid')
        ]

        if not needs_verification:
            logger.info('verify_store_names_skip_none_needed')
            return records

        logger.info(
            'verify_store_names_start',
            count=len(needs_verification),
            naver_available=bool(self.settings.naver_client_id),
            kakao_available=bool(self.settings.kakao_rest_api_key),
        )

        verified_count = 0
        failed_count = 0

        async with httpx.AsyncClient() as client:
            sem = asyncio.Semaphore(max_concurrency)

            async def verify_one(record: CertRecord) -> None:
                nonlocal verified_count, failed_count

                async with sem:
                    query = build_search_query(
                        record.province,
                        record.municipality,
                        record.emd,
                        record.road_name or record.raw_address,
                        record.biz_type,
                        record.raw_name,
                    )

                    verified_name = await self._verify_store(client, query)

                    if verified_name:
                        old_name = record.raw_name
                        record.raw_name = verified_name
                        record.store_name_quality = 'verified'
                        verified_count += 1
                        logger.info(
                            'verify_store_name_updated',
                            old_name=old_name,
                            new_name=verified_name,
                        )
                    else:
                        failed_count += 1
                        logger.warning(
                            'verify_store_name_not_found',
                            store_name=record.raw_name,
                        )

                    await asyncio.sleep(0.3)

            chunk_size = 20
            for i in range(0, len(needs_verification), chunk_size):
                chunk = needs_verification[i : i + chunk_size]
                await asyncio.gather(*[verify_one(r) for r in chunk])
                logger.info(
                    'verify_store_names_progress',
                    processed=min(i + chunk_size, len(needs_verification)),
                    total=len(needs_verification),
                    verified=verified_count,
                    failed=failed_count,
                )

        self.cache.save()
        logger.info(
            'verify_store_names_complete',
            verified=verified_count,
            failed=failed_count,
            total=len(needs_verification),
        )
        return records

    async def _verify_store(self, client: httpx.AsyncClient, query: str) -> str | None:
        """단일 가게명 검증 (캐시 우선 + 어댑터 위임)"""
        cached = self.cache.get(query)
        if cached is not None:
            return cached if cached else None

        naver_task = asyncio.create_task(self.adapter.search_naver(query, client))
        kakao_task = asyncio.create_task(self.adapter.search_kakao(query, client))

        result = None
        done, pending = await asyncio.wait(
            [naver_task, kakao_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in done:
            try:
                task_result = task.result()
                if task_result:
                    result = task_result
                    break
            except Exception:
                pass

        if result:
            for task in pending:
                task.cancel()
        else:
            for task in pending:
                try:
                    task_result = await task
                    if task_result:
                        result = task_result
                        break
                except (asyncio.CancelledError, Exception):
                    pass

        self.cache.set(query, result)
        return result


# ═══════════════════════════════════════════════════════════
# 진입점
# ═══════════════════════════════════════════════════════════


async def verify_store_names(records: list[CertRecord]) -> list[CertRecord]:
    """가게명 검증 진입점"""
    service = StoreVerifyService()
    return await service.verify_batch(records)
