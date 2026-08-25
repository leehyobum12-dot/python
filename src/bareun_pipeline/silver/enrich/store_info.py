"""
Bareun Pipeline: Silver Enrichment - Store Info Enrichment
===========================================================
- 가게 정보 보강 오케스트레이션 (전화번호, 주소, 링크)
- 네이버 로컬 검색 + 카카오 키워드 검색
- 캐시 관리 및 배치 처리
- I/O는 adapters/local_search_client.py에 위임

이관 출처: silver/enrich.py (StoreInfoEnricher)
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import threading
from datetime import datetime

import httpx

from ...adapters.local_search_client import LocalSearchAdapter
from ...domain.record import CertRecord
from ...observability.logging import get_logger
from ...settings import get_settings

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════
# 캐시
# ═══════════════════════════════════════════════════════════


class StoreInfoCache:
    """가게 정보 보강 캐시 (파일 기반)"""

    def __init__(self, cache_path: str):
        self._path = cache_path
        self._cache: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if os.path.exists(self._path):
            try:
                with open(self._path, encoding='utf-8') as f:
                    self._cache = json.load(f)
                logger.info('store_info_cache_loaded', count=len(self._cache))
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
            logger.warning('store_info_cache_save_failed', error=str(e))

    def get(self, key: str) -> dict | None:
        with self._lock:
            return self._cache.get(key)

    def set(self, key: str, value: dict) -> None:
        with self._lock:
            self._cache[key] = value


# ═══════════════════════════════════════════════════════════
# 가게 정보 보강 서비스
# ═══════════════════════════════════════════════════════════


class StoreInfoEnrichService:
    """가게 정보 보강 서비스 (어댑터 오케스트레이션)"""

    def __init__(self):
        self.settings = get_settings()
        self.adapter = LocalSearchAdapter(settings=self.settings)
        self.cache = StoreInfoCache(
            os.path.join(self.settings.cache_dir, 'store_info', 'store_info_cache.json')
        )

    def _build_query(self, record: CertRecord) -> str:
        parts = []
        if record.road_name:
            parts.append(record.road_name)
        elif record.raw_address:
            parts.append(record.raw_address)

        if record.raw_name and len(record.raw_name) > 2:
            parts.append(record.raw_name)

        return ' '.join(parts[:2])

    async def enrich_batch(
        self,
        records: list[CertRecord],
        max_concurrency: int = 3,
    ) -> list[CertRecord]:
        """가게 정보 보강 배치 처리"""
        logger.info('store_info_enrich_start', count=len(records))
        enriched_count = 0

        async with httpx.AsyncClient(timeout=30) as client:
            sem = asyncio.Semaphore(max_concurrency)

            async def enrich_one(record: CertRecord) -> None:
                nonlocal enriched_count

                async with sem:
                    query = self._build_query(record)
                    cache_key = f"{record.raw_address or ''}|{record.raw_name or ''}"

                    cached = self.cache.get(cache_key)
                    if cached is not None:
                        self._apply_info(record, cached)
                        if cached:
                            enriched_count += 1
                        await asyncio.sleep(0.05)
                        return

                    result = await self._fetch_info(client, query)
                    self.cache.set(cache_key, result)
                    self._apply_info(record, result)
                    if result:
                        enriched_count += 1

                    await asyncio.sleep(0.2)

            chunk_size = 20
            for i in range(0, len(records), chunk_size):
                chunk = records[i : i + chunk_size]
                await asyncio.gather(*[enrich_one(r) for r in chunk])
                logger.info(
                    'store_info_enrich_progress',
                    processed=min(i + chunk_size, len(records)),
                    total=len(records),
                    enriched=enriched_count,
                )

        self.cache.save()
        logger.info(
            'store_info_enrich_complete',
            enriched=enriched_count,
            total=len(records),
        )
        return records

    async def _fetch_info(self, client: httpx.AsyncClient, query: str) -> dict:
        """네이버/카카오에서 가게 정보 추출 시도"""
        naver_task = asyncio.create_task(self._fetch_naver_info(client, query))
        kakao_task = asyncio.create_task(self._fetch_kakao_info(client, query))

        done, pending = await asyncio.wait(
            [naver_task, kakao_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        result = {}
        for task in done:
            try:
                task_result = task.result()
                if task_result:
                    result = task_result
                    break
            except Exception:
                pass

        if not result:
            for task in pending:
                try:
                    task_result = await task
                    if task_result:
                        result = task_result
                        break
                except (asyncio.CancelledError, Exception):
                    pass
        else:
            for task in pending:
                task.cancel()

        return result

    async def _fetch_naver_info(self, client: httpx.AsyncClient, query: str) -> dict:
        """네이버 로컬 검색에서 가게 정보 추출"""
        items = await self.adapter.search_naver_items(query, client, display=3)
        if not items:
            return {}

        item = items[0]
        title = re.sub(r'<[^>]+>', '', item.get('title', '')).strip()
        link = item.get('link', '')
        description = item.get('description', '').strip()
        telephone = item.get('telephone', '').strip()
        address = item.get('address', '').strip()
        road_address = item.get('roadAddress', '').strip()

        return {
            'title': title,
            'link': link,
            'description': description,
            'telephone': telephone,
            'address': address,
            'road_address': road_address,
            'photos': [],
            'source': 'naver_local',
        }

    async def _fetch_kakao_info(self, client: httpx.AsyncClient, query: str) -> dict:
        """카카오 키워드 검색에서 가게 정보 추출"""
        documents = await self.adapter.search_kakao_documents(query, client, size=3)
        if not documents:
            return {}

        doc = documents[0]
        place_name = doc.get('place_name', '').strip()
        place_url = doc.get('place_url', '')
        phone = doc.get('phone', '').strip()
        address_name = doc.get('address_name', '').strip()
        road_address_name = doc.get('road_address_name', '').strip()
        category_name = doc.get('category_name', '').strip()

        return {
            'title': place_name,
            'link': place_url,
            'telephone': phone,
            'address': address_name,
            'road_address': road_address_name,
            'category': category_name,
            'photos': [],
            'source': 'kakao_local',
        }

    def _apply_info(self, record: CertRecord, data: dict) -> None:
        """가게 정보 적용"""
        if not data:
            return

        if not record.phone and data.get('telephone'):
            record.phone = data['telephone']

        if data.get('road_address') and not record.road_name:
            record.road_name = data['road_address']
        if data.get('address') and not record.lot_num:
            record.lot_num = data['address']

        if record.raw is None:
            record.raw = {}
        if isinstance(record.raw, dict):
            record.raw['external_info'] = {
                'link': data.get('link'),
                'photos': data.get('photos', []),
                'source': data.get('source'),
                'fetched_at': datetime.now().isoformat(),
            }


# ═══════════════════════════════════════════════════════════
# 진입점
# ═══════════════════════════════════════════════════════════


async def enrich_store_info(records: list[CertRecord]) -> list[CertRecord]:
    """가게 정보 보강 진입점"""
    service = StoreInfoEnrichService()
    return await service.enrich_batch(records)
