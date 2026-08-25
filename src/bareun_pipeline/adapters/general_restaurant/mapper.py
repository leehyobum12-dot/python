# ==============================================================================
# 파일 2: src/bareun_pipeline/adapters/general_restaurant/mapper.py
# ==============================================================================
"""원본 데이터 → CertRecord 매핑 (순수 함수)"""

from __future__ import annotations

from typing import Any

from ...domain.address import parse_addr_tokens
from ...domain.record import CertRecord
from .coordinates import convert_to_wgs84


def extract_hygiene_grade(grade: str) -> str | None:
    """위생등급 추출 (갑/을/병) - 버그 수정: 중복 접두사 제거"""
    grade = (grade or '').strip()
    return grade if grade in ('갑', '을', '병') else None


def map_item_to_record(
    item: dict[str, Any], source: str, cert_type: str, province_prefixes: tuple
) -> CertRecord | None:
    """단일 아이템을 CertRecord로 변환"""
    raw_name = str(item.get('BPLC_NM', '')).strip()
    raw_address = str(item.get('ROAD_NM_ADDR', '') or item.get('LOTNO_ADDR', '')).strip()

    if not raw_name or not raw_address:
        return None

    status = str(item.get('SALS_STTS_NM', '')).strip()
    is_active = '폐업' not in status and '취소' not in status
    if not is_active:
        return None

    p, m, e = parse_addr_tokens(raw_address)
    if province_prefixes and p and p not in province_prefixes:
        return None

    lat, lng = None, None
    x_val, y_val = item.get('CRD_INFO_X'), item.get('CRD_INFO_Y')
    if x_val and y_val:
        try:
            lat, lng = convert_to_wgs84(float(x_val), float(y_val))
        except (ValueError, TypeError):
            pass

    hygiene_grade = extract_hygiene_grade(str(item.get('GRD_SE_NM', '')).strip())
    certs = [cert_type]
    if hygiene_grade:
        certs.append(f'hygiene_grade_{hygiene_grade}')  # 🐛 버그 수정: 중복 방지

    return CertRecord(
        source=source,
        cert_type=cert_type,
        raw_name=raw_name,
        raw_address=raw_address,
        province=p,
        municipality=m,
        emd=e,
        road_name=str(item.get('ROAD_NM_ADDR', '')).strip() or None,
        lot_num=str(item.get('LOTNO_ADDR', '')).strip() or None,
        lat=lat,
        lng=lng,
        phone=str(item.get('TELNO', '')).strip() or None,
        biz_type=str(item.get('BZSTAT_SE_NM', '')).strip() or None,
        designated_at=str(item.get('LCPMT_YMD', '')).strip() or None,
        closed_at=str(item.get('CLSBIZ_YMD', '')).strip() or None,
        is_active=is_active,
        hygiene_grade=hygiene_grade,
        certs=certs,
        raw=item,
    )
