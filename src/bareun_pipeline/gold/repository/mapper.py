"""
Bareun Pipeline: Gold Repository Mapper (Production Schema Aligned)
====================================================================
- CertRecord → `restaurants` 테이블 JSONB Payload 변환
- `bulk_upsert_restaurants` RPC 함수 시그니처 준수
- PII 마스킹 적용 (개인정보보호법 준수)
- is_active → operational_status 파생 계산
- closed_at str → date 변환
- v5.1: operational_status 3-state enum 지원

위치: src/bareun_pipeline/gold/repository/mapper.py
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from ...domain.privacy import hash_phone
from ...domain.record import CertRecord


def _parse_date(date_str: str | None) -> date | None:
    """문자열 날짜를 date 객체로 변환 (YYYYMMDD 또는 YYYY-MM-DD)"""
    if not date_str or not isinstance(date_str, str):
        return None
    try:
        # YYYYMMDD 형식 (공공데이터 API 표준)
        if len(date_str) == 8 and date_str.isdigit():
            return datetime.strptime(date_str, '%Y%m%d').date()
        # YYYY-MM-DD 형식 (수동 입력)
        if len(date_str) == 10 and date_str[4] == '-':
            return datetime.strptime(date_str, '%Y-%m-%d').date()
    except (ValueError, IndexError):
        pass
    return None


def _derive_operational_status(record: CertRecord) -> str:
    """
    CertRecord 상태 → operational_status enum 파생 계산

    Returns:
        'operational' | 'permanently_closed'
        ※ 일시휴업(temporarily_closed)은 파이프라인에서 감지 불가.
          사장님 신고 또는 관리자 수동 처리로만 설정됨.
    """
    closed_at = getattr(record, 'closed_at', None)
    is_active = getattr(record, 'is_active', True)

    if closed_at or not is_active:
        return 'permanently_closed'
    return 'operational'


def _extract_business_hours(record: CertRecord) -> dict:
    """
    business_hours 우선순위 추출

    우선순위:
    1. record.business_hours 직접 속성
    2. raw.external_info.business_hours
    3. raw.tourist_business_hours (Tourist 병합 데이터)
    4. {} (빈 딕셔너리)
    """
    direct = getattr(record, 'business_hours', None)
    if direct:
        return direct

    if isinstance(record.raw, dict):
        raw_ext = record.raw.get('external_info') or {}
        if isinstance(raw_ext, dict) and raw_ext.get('business_hours'):
            return raw_ext['business_hours']

        if record.raw.get('tourist_business_hours'):
            return record.raw['tourist_business_hours']

    return {}


def _extract_photos(record: CertRecord) -> list:
    """
    photos 우선순위 추출

    우선순위:
    1. record.photos 직접 속성
    2. raw.external_info.photos
    3. raw.tourist_photos (Tourist 병합 데이터)
    4. [] (빈 배열)
    """
    direct = getattr(record, 'photos', None)
    if direct:
        return direct

    if isinstance(record.raw, dict):
        raw_ext = record.raw.get('external_info') or {}
        if isinstance(raw_ext, dict) and raw_ext.get('photos'):
            return raw_ext['photos']

        if record.raw.get('tourist_photos'):
            return record.raw['tourist_photos']

    return []


def _safe_menus(menus: list | None) -> list[str]:
    """메뉴 배열에서 유효한 문자열만 필터링"""
    if not menus:
        return []
    return [m.strip() for m in menus if isinstance(m, str) and m.strip()]


def to_db_row(record: CertRecord) -> dict[str, Any]:
    """
    CertRecord를 bulk_upsert_restaurants RPC Payload로 변환

    실제 DB 스키마 (restaurants 테이블, v5.1)에 정확히 매핑합니다.
    """
    # 파생 계산 필드
    closed_at = _parse_date(getattr(record, 'closed_at', None))
    operational_status = _derive_operational_status(record)
    business_hours = _extract_business_hours(record)
    photos = _extract_photos(record)

    row = {
        # ─── 기본 정보 ───
        'store_name': record.raw_name,
        'province': record.province,
        'municipality': record.municipality,
        'emd': record.emd,
        'address_road': record.road_name,
        'address_lotnum': record.lot_num,
        # ─── 메뉴 정보 ───
        'main_menu': record.main_menu,
        'main_menu_source': record.main_menu_source,
        'menus': _safe_menus(record.menus),
        'menus_source': record.menus_source,
        # ─── 품질 점수 (smallint 캐스팅) ───
        'quality_score': int(record.quality_score or 0),
        # ─── 좌표 (geog는 DB 트리거가 자동 생성) ───
        'lat': record.lat,
        'lng': record.lng,
        # ─── 연락처 (PII 마스킹 적용) ───
        'phone': hash_phone(record.phone) if record.phone else None,
        # ─── 업종 ───
        'biz_type': record.biz_type,
        # ─── 인증 배지 (text[] 타입) ───
        'certs': record.certs or [],
        # ─── 소스 및 식별자 ───
        'data_source': record.source,
        'designated_at': _parse_date(record.designated_at),
        'dedup_key': record.dedup_key,
        # ─── 7차원 태그 배열 ───
        'cuisine_tags': record.cuisine_tags or [],
        'nutrient_tags': record.nutrient_tags or [],
        'cooking_tags': record.cooking_tags or [],
        'ingredient_tags': record.ingredient_tags or [],
        'regional_tags': record.regional_tags or [],
        'allergen_tags': record.allergen_tags or [],
        'occasion_tags': record.occasion_tags or [],
        'avoid_tags': record.avoid_tags or [],
        # ─── v5.1 신규 필드 ───
        'hygiene_grade': getattr(record, 'hygiene_grade', None),
        'closed_at': closed_at,
        'operational_status': operational_status,
        'business_hours': business_hours,
        'photos': photos,
    }

    return row


def to_db_rows(records: list[CertRecord]) -> list[dict[str, Any]]:
    """배치 변환 (유효한 레코드만 필터링)"""
    return [to_db_row(r) for r in records if r.is_valid() and r.dedup_key]
