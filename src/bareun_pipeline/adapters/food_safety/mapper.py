"""
Bareun Pipeline: Food Safety Mapper
====================================
- 원본 데이터(Dict) → CertRecord 도메인 모델 변환
- FIELD_CANDIDATES 기반 유연한 필드 매핑 (스키마 변경 대응)
- 지역(Province) 필터링 및 PII 필드 식별
- I/O 없음, 외부 의존성 없음

위치: src/bareun_pipeline/adapters/food_safety/mapper.py
"""
from __future__ import annotations

from typing import Any, Optional

from ...domain.address import match_prefixes, parse_addr_tokens
from ...domain.record import CertRecord


# 필드 후보 (스키마 변경 대응 SSOT)
FIELD_CANDIDATES: dict[str, tuple[str, ...]] = {
    'name': ('BSSH_NM', 'UPSO_NM', 'ENTRPS_NM', '업소명', '상호명', '영업소명'),
    'addr': ('ADDR', 'SITE_ADDR', 'RDNMADR', 'ROAD_NM_ADDR', '도로명주소', '소재지도로명주소', '영업장소재지', '소재지'),
    'lot': ('JIBUN_ADDR', 'LOT_ADDR', '지번주소'),
    'phone': ('TELNO', 'TEL', '전화번호', '업소전화번호'),
    'biz': ('INDUTY_NM', 'INDUTY_CD_NM', '업종명', '업태구분명'),
    'designated': ('ASGN_FROM', 'ASGN_YMD', 'PRMISN_YMD', '지정일자', '허가일자', '지정시작일자'),
    'status': ('CLSBIZ_DVS_CD_NM', '영업상태', 'BIZ_STT_CD_NM'),
    'closed': ('CLSBIZ_DT', '폐업일자', 'CLOSE_DT'),
    'cancel': ('ASGN_CANCEL_YMD', '지정취소일자'),
    'menu': ('MAIN_MENU', 'MENU_NM', '대표메뉴', '주메뉴'),
}


def get_field(item: dict[str, Any], field_name: str) -> str:
    """필드 후보 목록에서 첫 번째로 매칭되는 값 추출"""
    if not isinstance(item, dict):
        return ''
    for key in FIELD_CANDIDATES.get(field_name, ()):
        val = item.get(key)
        if val:
            return str(val).strip()
    return ''


def map_to_cert_records(
    rows: list[dict[str, Any]], 
    province: Optional[str] = None,
    source: str = 'food_safety',
    cert_type: str = 'food_safety',
) -> list[CertRecord]:
    """
    원본 데이터 리스트를 CertRecord 리스트로 변환
    
    Args:
        rows: API에서 수집된 원본 딕셔너리 리스트
        province: 필터링할 시도명 (None이면 전국)
        source: 데이터 소스 식별자
        cert_type: 인증 타입
        
    Returns:
        변환된 CertRecord 리스트 (유효성 검증 통과 건만)
    """
    records: list[CertRecord] = []
    prefixes = match_prefixes(province) if province else None

    for item in rows:
        addr = get_field(item, 'addr')
        if not addr:
            continue

        # 지역 필터링
        if prefixes and not addr.startswith(prefixes):
            continue

        p, m, e = parse_addr_tokens(addr)
        
        clsbiz_dt = get_field(item, 'closed')
        cancel_dt = get_field(item, 'cancel')
        status = get_field(item, 'status')
        
        # 영업 상태 판별 로직
        is_active = ('폐업' not in status and not clsbiz_dt and not cancel_dt)

        record = CertRecord(
            source=source,
            cert_type=cert_type,
            raw_name=get_field(item, 'name'),
            raw_address=addr,
            province=p,
            municipality=m,
            emd=e,
            road_name=addr,  # 도로명주소가 없으면 지번주소라도 할당
            lot_num=get_field(item, 'lot') or '',
            main_menu=get_field(item, 'menu'),
            phone=get_field(item, 'phone'),
            biz_type=get_field(item, 'biz'),
            designated_at=get_field(item, 'designated'),
            cancelled_at=cancel_dt or None,
            closed_at=clsbiz_dt or None,
            is_active=is_active,
            raw=item,  # 원본 데이터 보존 (Bronze 아카이빙 및 디버깅용)
        )
        
        # 도메인 모델의 자체 검증 로직 통과 시에만 추가
        if record.is_valid():
            records.append(record)

    return records