"""
Bareun Pipeline: Silver Enrichment - Menu Enrichment
=====================================================
- 메뉴 보강 오케스트레이션 (네이버/카카오 + 향후 관광공사 통합)
- 메뉴 유효성 검증 (허수 제거)
- 캐시 관리 및 배치 처리
- I/O는 adapters/local_search_client.py에 위임

이관 출처: silver/enrich.py (MenuEnricher + is_valid_menu_name)
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import threading
from difflib import SequenceMatcher
from typing import Optional

import httpx

from ...domain.record import CertRecord
from ...adapters.local_search_client import LocalSearchAdapter
from ...observability.logging import get_logger
from ...settings import get_settings

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════
# 메뉴 검증 휴리스틱
# ═══════════════════════════════════════════════════════════

INVALID_MENU_PATTERNS: list[str] = [
    r'^[0-9,]+원$', r'^\d+인분$', r'^[가-힣]{1}$',
    r'^세트$', r'^식사$', r'^음료$',
    r'^메뉴$', r'^요리$', r'^코스$',
    r'^기본$', r'^추가$', r'^(서비스|무료|제공)$',
]

STORE_NAME_IN_MENU_THRESHOLD = 0.7


def is_valid_menu_name(menu_name: str, store_name: Optional[str] = None) -> bool:
    """메뉴명 유효성 검증 (허수 제거)"""
    if not menu_name or not isinstance(menu_name, str):
        return False

    menu = menu_name.strip()
    if len(menu) < 2 or len(menu) > 50:
        return False

    for pattern in INVALID_MENU_PATTERNS:
        if re.match(pattern, menu):
            return False

    valid_chars = len(re.findall(r'[\uAC00-\uD7A3a-zA-Z0-9]', menu))
    if valid_chars / len(menu) < 0.5:
        return False

    if store_name and len(store_name) >= 2:
        ratio = SequenceMatcher(None, menu, store_name).ratio()
        if ratio >= STORE_NAME_IN_MENU_THRESHOLD:
            return False

    return True


# ═══════════════════════════════════════════════════════════
# 캐시
# ═══════════════════════════════════════════════════════════

class MenuCache:
    """메뉴 보강 캐시 (파일 기반)"""

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
                logger.info('menu_cache_loaded', count=len(self._cache))
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
            logger.warning('menu_cache_save_failed', error=str(e))

    def get(self, key: str) -> Optional[dict]:
        with self._lock:
            return self._cache.get(key)

    def set(self, key: str, value: dict) -> None:
        with self._lock:
            self._cache[key] = value


# ═══════════════════════════════════════════════════════════
# 메뉴 보강 서비스
# ═══════════════════════════════════════════════════════════

class MenuEnrichService:
    """메뉴 보강 서비스 (어댑터 오케스트레이션)"""

    def __init__(self):
        self.settings = get_settings()
        self.adapter = LocalSearchAdapter(settings=self.settings)
        self.cache = MenuCache(
            os.path.join(self.settings.cache_dir, 'menu', 'menu_cache.json')
        )

    def _build_query(self, record: CertRecord) -> str:
        parts = []
        if record.road_name:
            parts.append(record.road_name)
        elif record.lot_num:
            parts.append(record.lot_num)
        elif record.raw_address:
            parts.append(record.raw_address)

        if record.biz_type:
            parts.append(record.biz_type)

        if (record.raw_name and len(record.raw_name) > 2
                and not any(re.match(p, record.raw_name) for p in INVALID_MENU_PATTERNS)):
            parts.append(record.raw_name)

        return ' '.join(parts[:3])

    async def enrich_batch(
        self,
        records: list[CertRecord],
        max_concurrency: int = 3,
    ) -> list[CertRecord]:
        """메뉴 보강 배치 처리"""
        needs_enrichment = [r for r in records if not r.main_menu or not r.menus]

        if not needs_enrichment:
            logger.info('menu_enrich_skip_all_have_menus')
            return records

        logger.info('menu_enrich_start', count=len(needs_enrichment))
        enriched_count = 0

        async with httpx.AsyncClient() as client:
            sem = asyncio.Semaphore(max_concurrency)

            async def enrich_one(record: CertRecord) -> None:
                nonlocal enriched_count

                async with sem:
                    query = self._build_query(record)
                    cache_key = f"{query}|{record.raw_name or ''}"

                    cached = self.cache.get(cache_key)
                    if cached is not None:
                        self._apply_menus(record, cached)
                        if cached.get('menus'):
                            enriched_count += 1
                        await asyncio.sleep(0.05)
                        return

                    result = await self._fetch_menus(client, query)
                    self.cache.set(cache_key, result)
                    self._apply_menus(record, result)
                    if result.get('menus'):
                        enriched_count += 1

                    await asyncio.sleep(0.2)

            chunk_size = 20
            for i in range(0, len(needs_enrichment), chunk_size):
                chunk = needs_enrichment[i:i + chunk_size]
                await asyncio.gather(*[enrich_one(r) for r in chunk])
                logger.info(
                    'menu_enrich_progress',
                    processed=min(i + chunk_size, len(needs_enrichment)),
                    total=len(needs_enrichment), enriched=enriched_count,
                )

        self.cache.save()
        logger.info(
            'menu_enrich_complete', enriched=enriched_count,
            total=len(needs_enrichment),
            note='네이버/카카오 로컬 API는 메뉴 정보를 제공하지 않습니다. TouristInfo 상세 조회를 활용하세요.',
        )
        return records

    async def _fetch_menus(self, client: httpx.AsyncClient, query: str) -> dict:
        """네이버/카카오에서 메뉴 추출 시도"""
        naver_task = asyncio.create_task(self._fetch_naver_menu(client, query))
        kakao_task = asyncio.create_task(self._fetch_kakao_menu(client, query))

        done, pending = await asyncio.wait(
            [naver_task, kakao_task], return_when=asyncio.FIRST_COMPLETED,
        )

        result = {'menus': [], 'source': None}
        for task in done:
            try:
                task_result = task.result()
                if task_result.get('menus'):
                    result = task_result
                    break
            except Exception:
                pass

        if not result['menus']:
            for task in pending:
                try:
                    task_result = await task
                    if task_result.get('menus'):
                        result = task_result
                        break
                except (asyncio.CancelledError, Exception):
                    pass
        else:
            for task in pending:
                task.cancel()

        return result

    async def _fetch_naver_menu(self, client: httpx.AsyncClient, query: str) -> dict:
        """네이버 로컬 검색에서 메뉴 추출 (description 파싱)"""
        items = await self.adapter.search_naver_items(query, client, display=5)
        if not items:
            return {'menus': [], 'source': None}

        menus = []
        source = None
        for item in items:
            title = re.sub(r'<[^>]+>', '', item.get('title', '')).strip()
            description = item.get('description', '').strip()

            if description:
                candidates = re.split(r'[,;|/\n·]', description)
                for candidate in candidates:
                    candidate = candidate.strip()
                    if candidate and 2 <= len(candidate) <= 50:
                        menus.append(candidate)

            if not source and title and len(title) > 2:
                source = 'naver_local'

        return {'menus': menus[:10], 'source': source}

    async def _fetch_kakao_menu(self, client: httpx.AsyncClient, query: str) -> dict:
        """카카오 키워드 검색에서 메뉴 추출 (실제로는 메뉴 없음)"""
        documents = await self.adapter.search_kakao_documents(query, client, size=5)
        if not documents:
            return {'menus': [], 'source': None}

        source = None
        for doc in documents:
            place_name = doc.get('place_name', '').strip()
            if not source and place_name and len(place_name) > 2:
                source = 'kakao_local'

        return {'menus': [], 'source': source}

    def _apply_menus(self, record: CertRecord, data: dict) -> None:
        """유효성 검증 후 레코드에 메뉴 적용"""
        menus = data.get('menus', [])
        source = data.get('source')

        if not menus:
            return

        valid_menus = [m for m in menus if is_valid_menu_name(m, record.raw_name)]
        if not valid_menus:
            return

        if record.main_menu:
            all_menus = sorted(set([record.main_menu] + valid_menus))
            record.menus = all_menus
            if not record.menus_source:
                record.menus_source = source
        else:
            record.main_menu = valid_menus[0]
            record.main_menu_source = source
            record.menus = sorted(set(valid_menus))
            record.menus_source = source


# ═══════════════════════════════════════════════════════════
# 진입점
# ═══════════════════════════════════════════════════════════

async def enrich_menus(records: list[CertRecord]) -> list[CertRecord]:
    """메뉴 보강 진입점"""
    service = MenuEnrichService()
    return await service.enrich_batch(records)