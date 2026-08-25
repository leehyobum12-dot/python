"""
PII (개인정보) 마스킹 모듈
============================
- raw_certifications 저장 전 비식별화
- 개인정보보호법 준수
- 어댑터별 PII 필드 동적 수집

기존 참고:
- storage/raw_archiver.py의 _redact_pii() 함수
- PII_FIELDS 하드코딩 방식 유지하되, 어댑터 메타데이터 기반 동적 수집 추가

보안 개선:
- 기존: PII_FIELDS를 raw_archiver.py에 하드코딩
- 개선: 어댑터 클래스의 pii_fields 속성에서 동적 수집 + 범용 필드 통합
"""

from __future__ import annotations

from typing import Any

# ─────────────── 범용 PII 필드 (모든 소스 공통) ───────────────
UNIVERSAL_PII_FIELDS: set[str] = {
    # 대표자/영업자
    'PRSDNT_NM',
    'REPRESENT',
    'OWNER_NAME',
    '대표자',
    '대표자명',
    '영업자명',
    '대표자실명',
    # 사업자/종사자 식별번호
    'BRN',
    'BIZ_REG_NO',
    'WRKR_REG_NO',
    '사업자등록번호',
    '종사자등록번호',
    # 개인 연락처 (식당 영업전화와 구분 필요)
    '개인전화',
    '핸드폰',
    '모바일',
}

# ─────────────── 어댑터별 PII 필드 ───────────────
# 기존 storage/raw_archiver.py의 PII_FIELDS 보존
PII_FIELDS_BY_SOURCE: dict[str, set[str]] = {
    'safe_restaurant': {
        'RELAX_RSTRNT_REPRESENT',  # 대표자 실명
    },
    'food_safety': {
        'PRSDNT_NM',  # 대표자명
        'WRKR_REG_NO',  # 종사자 등록번호
    },
    'low_sodium': {
        'RPRS_NM',  # 대표자명
    },
    'model_restaurant': {
        '대표자명',
        '영업자명',
    },
}

PII_MASK = '[비식별화]'


def get_pii_fields(source: str) -> set[str]:
    """
    소스별 PII 필드 목록 반환

    Args:
        source: 어댑터 소스명 (e.g., 'safe_restaurant')

    Returns:
        해당 소스의 PII 필드 집합
    """
    fields = set(UNIVERSAL_PII_FIELDS)
    fields.update(PII_FIELDS_BY_SOURCE.get(source, set()))
    return fields


def register_pii_fields(source: str, fields: set[str]) -> None:
    """
    어댑터별 PII 필드 동적 등록

    새로운 어댑터 추가 시 이 함수로 PII 필드를 등록할 수 있습니다.

    Args:
        source: 어댑터 소스명
        fields: PII 필드 집합
    """
    if source not in PII_FIELDS_BY_SOURCE:
        PII_FIELDS_BY_SOURCE[source] = set()
    PII_FIELDS_BY_SOURCE[source].update(fields)


def redact_pii(rows: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
    """
    원본 데이터에서 PII 필드 마스킹

    기존 storage/raw_archiver.py의 _redact_pii() 함수 보존

    Args:
        rows: 원본 데이터 목록
        source: 어댑터 소스명

    Returns:
        PII 필드가 마스킹된 데이터 목록
    """
    pii_fields = get_pii_fields(source)

    result = []
    for row in rows:
        if not isinstance(row, dict):
            result.append(row)
            continue

        masked_row = dict(row)
        for key in pii_fields:
            if masked_row.get(key):
                masked_row[key] = PII_MASK

        result.append(masked_row)

    return result


def hash_phone_for_storage(phone: str) -> str:
    """
    저장용 전화번호 해시 (검색 불가능, 통계용)

    Args:
        phone: 전화번호

    Returns:
        salted SHA-256 해시 (16자리)
    """
    from .dedup import hash_phone

    return hash_phone(phone)


def mask_phone(phone: str) -> str:
    """
    전화번호 마스킹 (표시용)

    예: 02-1234-5678 → 02-****-5678

    Args:
        phone: 전화번호

    Returns:
        마스킹된 전화번호
    """
    if not phone:
        return ''

    cleaned = str(phone).strip()

    # 하이픈이 있는 경우
    if '-' in cleaned:
        parts = cleaned.split('-')
        if len(parts) == 3:
            return f'{parts[0]}-****-{parts[2]}'
        elif len(parts) == 2:
            return f'{parts[0]}-****'

    # 숫자만 있는 경우
    digits = ''.join(c for c in cleaned if c.isdigit())
    if len(digits) >= 8:
        return f'{digits[:3]}****{digits[-4:]}'

    return '****'
