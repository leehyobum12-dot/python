"""
Bareun Pipeline: Silver Enrichment Orchestrator
================================================
- 분리된 4개의 보강 모듈(geocode, store_verify, menu, store_info)을 조율
- 기존 SilverEnricher 인터페이스를 유지하여 하위 호환성 확보
- avoid_tags 및 store_name_quality 등 도메인 로직 적용

이 모듈은 기존 1399줄의 silver/enrich.py(God Object)를 대체하는 최종 진입점입니다.
외부 API 호출(I/O)은 adapters 계층에 위임하고, 이 패키지는 오케스트레이션만 담당합니다.

Architecture:
    [Silver Layer]
        ├── __init__.py (Orchestrator & Re-exports)
        ├── geocode.py (Coordinates & Anomaly Detection)
        ├── store_verify.py (Store Name Verification)
        ├── menu.py (Menu Extraction & Validation)
        └── store_info.py (Contact & Metadata Enrichment)
"""
from __future__ import annotations

from typing import List

from ...domain.avoid_tags import assess_store_name_quality, generate_avoid_tags
from ...domain.record import CertRecord
from ...observability.logging import get_logger

# 하위 모듈의 진입점 함수들 (I/O 위임 대상)
from .geocode import enrich_coordinates as _enrich_coordinates
from .menu import enrich_menus as _enrich_menus
from .store_info import enrich_store_info as _enrich_store_info
from .store_verify import verify_store_names as _verify_store_names

logger = get_logger(__name__)

__all__ = [
    # Main Orchestrator
    "SilverEnricher",
    # Re-exported entry points for backward compatibility
    "enrich_coordinates",
    "verify_store_names",
    "enrich_menus",
    "enrich_store_info",
]


class SilverEnricher:
    """
    Silver Layer 통합 보강기 (Orchestrator)
    
    기존 silver/enrich.py의 God Object를 대체합니다.
    실제 I/O 및 무거운 로직은 하위 모듈에 위임하고, 
    이 클래스는 실행 순서와 도메인 규칙(avoid_tags 등)만 통제합니다.
    """

    def __init__(self):
        """
        오케스트레이터 초기화.
        설정 및 무거운 인스턴스 생성은 하위 모듈의 서비스 클래스에서 Lazy하게 수행됩니다.
        """
        self._logger = logger

    async def enrich(
        self,
        records: List[CertRecord],
        verify_store_names: bool = True,
        enrich_menus: bool = True,
        enrich_store_info: bool = True,
        force_geocoding: bool = False,
    ) -> List[CertRecord]:
        """
        전체 보강 파이프라인 실행
        
        Args:
            records: 보강 대상 레코드 목록
            verify_store_names: 가게명 검증 수행 여부
            enrich_menus: 메뉴 보강 수행 여부
            enrich_store_info: 가게 정보 보강 수행 여부
            force_geocoding: 기존 좌표 무시하고 강제 지오코딩 수행 여부
            
        Returns:
            보강이 완료된 CertRecord 목록
        """
        self._logger.info('silver_enrich_start', count=len(records))

        # 1. 지오코딩 및 좌표 이상치 탐지 (adapters/geocode_client 위임)
        records = await _enrich_coordinates(records, force=force_geocoding)

        # 2. 알레르기 태그 기반 avoid_tags 생성 (초기 평가)
        for record in records:
            if record.allergen_tags:
                record.avoid_tags = generate_avoid_tags(
                    allergen_tags=record.allergen_tags,
                    biz_type=record.biz_type,
                    main_menu=record.main_menu,
                    menus=record.menus,
                )

        # 3. 가게명 품질 초기 평가 (순수 도메인 로직)
        for record in records:
            record.store_name_quality = assess_store_name_quality(
                record.raw_name,
                record.province,
                record.municipality,
            )

        # 4. 가게명 검증 (필요한 경우에만 외부 API 호출 - adapters/local_search_client)
        if verify_store_names:
            records = await _verify_store_names(records)

        # 5. 메뉴 보강 (adapters/local_search_client 위임)
        if enrich_menus:
            records = await _enrich_menus(records)

        # 6. 가게 정보 보강 (전화번호, 링크 등 - adapters/local_search_client 위임)
        if enrich_store_info:
            records = await _enrich_store_info(records)

        self._logger.info('silver_enrich_complete', count=len(records))
        return records


# ═══════════════════════════════════════════════════════════
# 하위 호환성을 위한 진입점 래퍼 (Legacy Entry Points)
# ═══════════════════════════════════════════════════════════

async def enrich_coordinates(records: List[CertRecord]) -> List[CertRecord]:
    """지오코딩 단독 실행 (기존 인터페이스 호환)"""
    return await _enrich_coordinates(records)


async def verify_store_names(records: List[CertRecord]) -> List[CertRecord]:
    """가게명 검증 단독 실행 (기존 인터페이스 호환)"""
    return await _verify_store_names(records)


async def enrich_menus(records: List[CertRecord]) -> List[CertRecord]:
    """메뉴 보강 단독 실행 (기존 인터페이스 호환)"""
    return await _enrich_menus(records)


async def enrich_store_info(records: List[CertRecord]) -> List[CertRecord]:
    """가게 정보 보강 단독 실행 (기존 인터페이스 호환)"""
    return await _enrich_store_info(records)