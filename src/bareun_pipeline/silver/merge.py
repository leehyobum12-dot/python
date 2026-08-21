"""
Silver Layer: 단일 병합 로직 (SSOT)
=====================================
- dedup_key 기반 병합 (한 번만 수행)
- 인증 타입 통합
- 태그 배열 통합
- 메뉴/주소/좌표 우선순위 병합

기존 참고:
- pipeline.py의 merge_cert_types() 함수
- ingest_to_supabase.py의 consolidate() 함수

보존 사항:
- dedup_key 기반 병합
- 인증 타입 통합
- 메뉴 우선순위 (더 긴 메뉴 선택)
- 태그 배열 정렬

개선 사항:
- 단일 병합 로직 (SSOT) — 기존 이중 병합 제거
- Merger 클래스로 캡슐화
- 구조화 로깅
- 타입 안전성 강화
"""
from __future__ import annotations

from typing import Dict, List, Optional

from ..domain.record import CertRecord
from ..domain.dedup import generate_dedup_key
from ..observability.logging import get_logger


logger = get_logger(__name__)


class Merger:
    """
    레코드 병합기 (SSOT)

    사용법:
        merger = Merger()
        merged = merger.merge(records)
    """

    # 메뉴 출처 우선순위 (높을수록 우선)
    MENU_SOURCE_PRIORITY: Dict[str, int] = {
        'public_api': 4,
        'cross_source': 3,
        'naver_api': 2,
        'kakao_api': 2,
        'naver_blog': 2,
        'naver_cafe': 2,
        'naver_news': 1,
        'llm_verified': 3,
        'llm_filled': 2,
        'original': 1,
    }

    def merge(self, records: List[CertRecord]) -> List[CertRecord]:
        """
        레코드 병합 (dedup_key 기반)

        Args:
            records: 정규화된 레코드 목록

        Returns:
            병합된 레코드 목록
        """
        logger.info('merge_start', count=len(records))

        merged: Dict[str, CertRecord] = {}

        for record in records:
            if not record.is_valid():
                continue

            # dedup_key 생성 (아직 없는 경우)
            if not record.dedup_key:
                record.dedup_key = generate_dedup_key(
                    raw_name=record.raw_name,
                    raw_address=record.raw_address,
                    phone=record.phone,
                    detail_address=record.lot_num or record.road_name,
                )

            if not record.dedup_key:
                continue

            key = record.dedup_key

            if key not in merged:
                # 신규 레코드
                # certs를 리스트로 변환
                if record.cert_type and record.cert_type not in record.certs:
                    record.certs.append(record.cert_type)
                merged[key] = record
            else:
                # 기존 레코드와 병합
                existing = merged[key]
                self._merge_into(existing, record)

        result = list(merged.values())

        logger.info(
            'merge_complete',
            input_count=len(records),
            output_count=len(result),
            merged_count=len(records) - len(result),
        )

        return result

    def _merge_into(self, existing: CertRecord, new: CertRecord) -> None:
        """
        기존 레코드에 새 레코드 병합

        기존 pipeline.py의 merge_cert_types() 로직 보존

        Args:
            existing: 기존 레코드
            new: 새 레코드
        """
        # 인증 타입 통합
        if new.cert_type and new.cert_type not in existing.certs:
            existing.certs.append(new.cert_type)

        # 상호명: 더 유효하고 긴 이름 선택
        if self._is_better_name(new.raw_name, existing.raw_name):
            existing.raw_name = new.raw_name

        # 전화번호: 기존 값 유지 (없으면 새 값)
        if not existing.phone and new.phone:
            existing.phone = new.phone

        # 업종: 기존 값 유지 (없으면 새 값)
        if not existing.biz_type and new.biz_type:
            existing.biz_type = new.biz_type

        # 대표 메뉴: 우선순위 기반 선택
        self._merge_main_menu(existing, new)

        # 추가 메뉴 통합
        if new.menus:
            existing.menus = sorted(set(existing.menus + new.menus))
            if new.menus_source:
                existing.menus_source = new.menus_source

        # 날짜: 기존 값 유지 (없으면 새 값)
        if not existing.designated_at and new.designated_at:
            existing.designated_at = new.designated_at

        # 좌표: 기존 값 유지 (없으면 새 값)
        if not existing.has_coordinate() and new.has_coordinate():
            existing.lat = new.lat
            existing.lng = new.lng

        # 주소 3계층: 기존 값 유지 (없으면 새 값)
        if not existing.province and new.province:
            existing.province = new.province
        if not existing.municipality and new.municipality:
            existing.municipality = new.municipality
        if not existing.emd and new.emd:
            existing.emd = new.emd

        # 도로명/지번: 기존 값 유지 (없으면 새 값)
        if not existing.road_name and new.road_name:
            existing.road_name = new.road_name
        if not existing.lot_num and new.lot_num:
            existing.lot_num = new.lot_num

        # 원본 주소: 더 긴 주소 선택
        if new.raw_address and len(new.raw_address) > len(existing.raw_address or ''):
            existing.raw_address = new.raw_address

    def _merge_main_menu(self, existing: CertRecord, new: CertRecord) -> None:
        """
        대표 메뉴 병합 (우선순위 기반)

        기존 pipeline.py의 merge_cert_types() 메뉴 병합 로직 보존

        Args:
            existing: 기존 레코드
            new: 새 레코드
        """
        if not new.main_menu:
            return

        if not existing.main_menu:
            existing.main_menu = new.main_menu
            existing.main_menu_source = new.main_menu_source
            return

        # 우선순위 비교
        existing_priority = self.MENU_SOURCE_PRIORITY.get(
            existing.main_menu_source or '', 0
        )
        new_priority = self.MENU_SOURCE_PRIORITY.get(
            new.main_menu_source or '', 0
        )

        if new_priority > existing_priority:
            existing.main_menu = new.main_menu
            existing.main_menu_source = new.main_menu_source
        elif new_priority == existing_priority:
            # 같은 우선순위면 더 긴 메뉴 선택
            if len(new.main_menu) > len(existing.main_menu):
                existing.main_menu = new.main_menu
                existing.main_menu_source = new.main_menu_source

    def _is_better_name(self, new_name: str, existing_name: str) -> bool:
        """
        더 나은 상호명인지 판단

        Args:
            new_name: 새 상호명
            existing_name: 기존 상호명

        Returns:
            새 상호명이 더 나으면 True
        """
        from .normalize import Normalizer
        normalizer = Normalizer()

        new_valid = normalizer.is_valid_name(new_name)
        existing_valid = normalizer.is_valid_name(existing_name)

        if new_valid and not existing_valid:
            return True
        if new_valid and existing_valid:
            return len(new_name) > len(existing_name)
        return False


def merge_records(records: List[CertRecord]) -> List[CertRecord]:
    """
    레코드 목록 병합 (편의 함수)

    Args:
        records: 병합할 레코드 목록

    Returns:
        병합된 레코드 목록
    """
    merger = Merger()
    return merger.merge(records)