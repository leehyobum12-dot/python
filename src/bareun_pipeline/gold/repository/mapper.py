"""
Bareun Pipeline: Gold Repository Mapper
========================================
- CertRecord → Supabase Row 변환 (순수 함수)
- PostGIS geom 필드 생성 (WKT 형식)
- PII 마스킹 및 JSONB 직렬화
- 외부 I/O 없음, 테스트 용이성 확보

위치: src/bareun_pipeline/gold/repository/mapper.py
"""
from __future__ import annotations

from typing import Any, Optional

from ...domain.record import CertRecord
from ...domain.privacy import hash_phone


def to_db_row(record: CertRecord) -> dict[str, Any]:
    """
    CertRecord를 Supabase 테이블 Row로 변환
    
    이 함수는 순수 함수로, 외부 상태나 I/O에 의존하지 않습니다.
    모든 변환 로직은 멱등성(Upsert)을 보장하기 위해 결정론적이어야 합니다.
    """
    row = {
        # 식별자 (멱등성 키)
        "dedup_key": record.dedup_key,
        
        # 소스 및 타입
        "source": record.source,
        "cert_type": record.cert_type,
        
        # 기본 정보
        "raw_name": record.raw_name,
        "raw_address": record.raw_address,
        
        # 표준화된 주소
        "province": record.province,
        "municipality": record.municipality,
        "emd": record.emd,
        "road_name": record.road_name,
        "lot_num": record.lot_num,
        
        # 좌표 (PostGIS)
        "lat": record.lat,
        "lng": record.lng,
        
        # 연락처 (PII 마스킹)
        "phone": _mask_phone(record.phone),
        
        # 메뉴 및 영업 정보
        "main_menu": record.main_menu,
        "menus": record.menus or [],
        "business_hours": record.business_hours or {},
        "rest_days": record.rest_days or [],
        
        # 미디어 및 외부 정보
        "photos": record.photos or [],
        "external_info": record.external_info or {},
        
        # 상태
        "is_active": record.is_active,
    }
    
    # PostGIS geom 컬럼 생성 (WKT 형식)
    if record.lat is not None and record.lng is not None:
        row["geom"] = f"SRID=4326;POINT({record.lng} {record.lat})"
    else:
        row["geom"] = None
        
    return row


def _mask_phone(phone: Optional[str]) -> Optional[str]:
    """전화번호 마스킹 (도메인 프라이버시 모듈 위임)"""
    if not phone:
        return None
    return hash_phone(phone)


def to_db_rows(records: list[CertRecord]) -> list[dict[str, Any]]:
    """배치 변환 (리스트 내포 최적화)"""
    return [to_db_row(record) for record in records]