"""
Silver Layer: 데이터 정규화
============================
- 주소/전화/날짜 정규화
- 메뉴 분할 (콤마/슬래시)
- 손상된 상호명 복구

기존 참고:
- pipeline.py의 _split_menus() 함수
- pipeline.py의 recover_damaged_names() 함수
- pipeline.py의 norm_phone(), norm_name(), norm_addr() 함수

보존 사항:
- 메뉴 분할 로직
- 손상된 상호명 복구
- 전화번호 정규화

개선 사항:
- Normalizer 클래스로 캡슐화
- 구조화 로깅
- 타입 안전성 강화
"""

from __future__ import annotations

import re

from ..domain.record import CertRecord
from ..observability.logging import get_logger

logger = get_logger(__name__)


class Normalizer:
    """
    데이터 정규화기

    사용법:
        normalizer = Normalizer()
        record = normalizer.normalize(record)
    """

    # 메뉴 분할 패턴 (콤마, 슬래시, 한글 콤마)
    MENU_SPLIT_PATTERN = re.compile(r'[,/、]+')

    # 상호명 유효성 검사 패턴
    INVALID_NAME_PATTERN = re.compile(r'[?\ufffd]')

    # 일반 명사 (상호명으로 부적합)
    GENERIC_NAMES = {'식당', '카페', '음식점', '맛집'}

    def normalize(self, record: CertRecord) -> CertRecord:
        """
        레코드 정규화

        Args:
            record: 정규화할 레코드

        Returns:
            정규화된 레코드
        """
        # 상호명 정제
        if record.raw_name:
            record.raw_name = self._clean_name(record.raw_name)

        # 주소 정제
        if record.raw_address:
            record.raw_address = self._clean_address(record.raw_address)

        # 메뉴 분할 (main_menu가 콤마/슬래시로 구분된 경우)
        if record.main_menu:
            self._split_menus(record)

        return record

    def _clean_name(self, name: str) -> str:
        """상호명 정제"""
        if not name:
            return ''
        # 앞뒤 공백 제거
        name = name.strip()
        # 연속 공백 제거
        name = re.sub(r'\s+', ' ', name)
        return name

    def _clean_address(self, address: str) -> str:
        """주소 정제"""
        if not address:
            return ''
        # 앞뒤 공백 제거
        address = address.strip()
        # 연속 공백 제거
        address = re.sub(r'\s+', ' ', address)
        return address

    def _split_menus(self, record: CertRecord) -> None:
        """
        main_menu를 분할하여 menus에 추가

        기존 pipeline.py의 _split_menus() 함수 보존

        Args:
            record: 분할할 레코드
        """
        if not record.main_menu:
            return

        raw = record.main_menu
        parts = self.MENU_SPLIT_PATTERN.split(raw)
        parts = [p.strip() for p in parts if p.strip() and len(p.strip()) >= 2]

        if not parts:
            return

        record.main_menu = parts[0]
        if len(parts) > 1:
            existing = set(record.menus or [])
            for p in parts[1:6]:  # 최대 5개 추가 메뉴
                existing.add(p)
            record.menus = sorted(existing)

    def is_valid_name(self, name: str) -> bool:
        """
        상호명 유효성 검사

        기존 pipeline.py의 is_valid_name() 함수 보존

        Args:
            name: 검사할 상호명

        Returns:
            유효하면 True
        """
        if not name:
            return False
        s = name.strip()
        if len(s) < 2:
            return False
        if self.INVALID_NAME_PATTERN.search(s):
            return False
        if s in self.GENERIC_NAMES:
            return False
        return True


def recover_damaged_names(records: list[CertRecord]) -> int:
    """
    손상된 상호명 복구

    기존 pipeline.py의 recover_damaged_names() 함수 보존
    - 전화번호/주소 기반으로 손상된 상호명 복구

    Args:
        records: 복구할 레코드 목록

    Returns:
        복구된 레코드 수
    """
    normalizer = Normalizer()

    addr_map: dict[str, str] = {}
    phone_map: dict[str, str] = {}

    for r in records:
        if not normalizer.is_valid_name(r.raw_name):
            continue
        addr_key = _norm_addr(r.raw_address)
        addr_map.setdefault(addr_key, r.raw_name)
        np_ = _norm_phone(r.phone)
        if len(np_) >= 9:
            phone_map.setdefault(np_, r.raw_name)

    recovered = 0
    for r in records:
        if normalizer.is_valid_name(r.raw_name):
            continue

        clean = None
        np_ = _norm_phone(r.phone)
        if len(np_) >= 9 and np_ in phone_map:
            clean = phone_map[np_]
        elif _norm_addr(r.raw_address) in addr_map:
            clean = addr_map[_norm_addr(r.raw_address)]

        if clean:
            logger.debug(
                'name_recovered',
                original=r.raw_name,
                recovered=clean,
            )
            r.raw_name = clean
            recovered += 1

    if recovered > 0:
        logger.info('names_recovered', count=recovered)

    return recovered


def _norm_phone(p: str | None) -> str:
    """전화번호 정규화 (숫자만)"""
    return re.sub(r'\D', '', p or '')


def _norm_addr(addr: str) -> str:
    """주소 정규화 (괄호 제거, 공백 제거)"""
    s = re.sub(r'\([^)]*\)', '', addr or '')
    toks = s.split()
    if toks:
        from ..adapters.base import SIDO_ALIAS

        if toks[0] in SIDO_ALIAS:
            toks[0] = SIDO_ALIAS[toks[0]]
    return ''.join(toks).lower()


def normalize_records(records: list[CertRecord]) -> list[CertRecord]:
    """
    레코드 목록 정규화 (편의 함수)

    Args:
        records: 정규화할 레코드 목록

    Returns:
        정규화된 레코드 목록
    """
    normalizer = Normalizer()
    return [normalizer.normalize(r) for r in records]
