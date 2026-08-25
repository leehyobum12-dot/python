"""
Bareun Pipeline: Domain - Address Utilities
============================================
- 주소 판별 / 파싱 / 정규화 (순수 도메인 로직)
- 행정구역 표준화 (통계청 2026년 8월 기준)
- I/O 없음, 외부 의존성 없음 → 전 계층(Silver/Gold)에서 안전 import

이관 출처: adapters/base.py (주소 유틸 전체)
수정 사항:
  - ADDR_PATTERN 정규식 버그 수정 (군/구 단독 매칭 방지)
  - 변수 선언 순서 NameError 위험 제거
  - 전남광주통합특별시 제거 → 표준 행정구역 분리
  - 행정구역 매핑 기준일을 2026년 8월로 확정
"""

from __future__ import annotations

import re
from typing import Final

# ═══════════════════════════════════════════════════════════
# 토큰 판별 상수
# ═══════════════════════════════════════════════════════════

EMD_SUFFIXES: Final[tuple[str, ...]] = ('동', '읍', '면', '리', '가')
ROAD_SUFFIXES: Final[tuple[str, ...]] = ('로', '길', '대로')
SIGUNGU_SUFFIXES: Final[tuple[str, ...]] = ('시', '군', '구')
PROVINCE_SUFFIXES: Final[tuple[str, ...]] = (
    '특별시',
    '광역시',
    '특별자치도',
    '특별자치시',
    '도',
)


# ═══════════════════════════════════════════════════════════
# 행정구역 표준화 (통계청 기준, 2026년 8월)
# ═══════════════════════════════════════════════════════════

PROVINCE_CANON: Final[dict[str, str]] = {
    '서울': '서울특별시',
    '서울시': '서울특별시',
    '부산': '부산광역시',
    '부산시': '부산광역시',
    '대구': '대구광역시',
    '대구시': '대구광역시',
    '인천': '인천광역시',
    '인천시': '인천광역시',
    '광주': '광주광역시',
    '광주시': '광주광역시',
    '대전': '대전광역시',
    '대전시': '대전광역시',
    '울산': '울산광역시',
    '울산시': '울산광역시',
    '세종': '세종특별자치시',
    '세종시': '세종특별자치시',
    '경기': '경기도',
    '강원': '강원특별자치도',
    '강원도': '강원특별자치도',
    '충북': '충청북도',
    '충남': '충청남도',
    '전북': '전북특별자치도',
    '전북특별자치도': '전북특별자치도',
    '전라북도': '전북특별자치도',
    '전남': '전라남도',
    '경북': '경상북도',
    '경남': '경상남도',
    '제주': '제주특별자치도',
    '제주도': '제주특별자치도',
}

SIDO_ALIAS: Final[dict[str, str]] = PROVINCE_CANON


# ═══════════════════════════════════════════════════════════
# 정규식 (함수 정의 이전에 선언 → NameError 원천 차단)
# ═══════════════════════════════════════════════════════════

ADDR_PATTERN: Final[re.Pattern[str]] = re.compile(
    r'^(?:'
    r'(?P<special>서울|부산|대구|인천|광주|대전|울산)특별시|'
    r'(?P<sejong>세종특별자치시)|'
    r'(?P<province>경기도|강원특별자치도|충청북도|충청남도|'
    r'전북특별자치도|전라남도|경상북도|경상남도|제주특별자치도)'
    r')\s*'
    r'(?P<sigungu>[가-힣]+(?:시|군|구))?\s*'
    r'(?:(?P<eup>[가-힣]+읍)|(?P<myeon>[가-힣]+면)|(?P<dong>[가-힣]+동))?'
)


# ═══════════════════════════════════════════════════════════
# 토큰 판별
# ═══════════════════════════════════════════════════════════


def is_road_name(token: str | None) -> bool:
    """도로명 접미사(로/길/대로) 여부 판별."""
    if not token:
        return False
    return any(token.endswith(sf) for sf in ROAD_SUFFIXES)


def is_emd_token(token: str | None) -> bool:
    """읍면동 접미사 여부 판별 (도로명 제외)."""
    if not token:
        return False
    if is_road_name(token):
        return False
    return any(token.endswith(sf) for sf in EMD_SUFFIXES)


def extract_emd_from_addr(addr: str) -> str:
    """주소 문자열에서 읍면동 토큰 추출."""
    if not addr:
        return ''
    cleaned = re.sub(r'\([^)]*\)', '', addr)
    toks = cleaned.split()
    if len(toks) < 3:
        return ''
    for tok in toks[2:]:
        if is_emd_token(tok) and not any(tok.endswith(sf) for sf in SIGUNGU_SUFFIXES):
            return tok
    return ''


# ═══════════════════════════════════════════════════════════
# 행정구역 표준화 함수
# ═══════════════════════════════════════════════════════════


def canon_province(province: str | None) -> str | None:
    """시도명 표준화 (통계청 기준)."""
    if not province:
        return province
    stripped = province.strip()
    return PROVINCE_CANON.get(stripped, stripped)


def match_prefixes(province: str) -> tuple[str, ...]:
    """시도 접두사 매칭."""
    canonical = canon_province(province)
    if not canonical:
        return ()
    return (canonical,)


# ═══════════════════════════════════════════════════════════
# 주소 파싱 (3단계 fallback)
# ═══════════════════════════════════════════════════════════


def parse_addr_tokens(
    addr: str,
) -> tuple[str | None, str | None, str | None]:
    """
    주소를 (시도, 시군구, 읍면동) 튜플로 분해.
    """
    if not addr:
        return None, None, None

    addr = re.sub(r'\s+', ' ', addr.strip())

    match = ADDR_PATTERN.match(addr)
    if match:
        province = match.group('special') or match.group('sejong') or match.group('province')
        municipality = match.group('sigungu')
        emd = match.group('eup') or match.group('myeon') or match.group('dong')

        if province:
            province = canon_province(province)
        return province, municipality, emd

    tokens = addr.split()
    province: str | None = None
    municipality: str | None = None
    emd: str | None = None

    if tokens and any(tokens[0].endswith(sf) for sf in PROVINCE_SUFFIXES):
        province = canon_province(tokens[0])
        tokens = tokens[1:]

    if tokens and province and any(tokens[0].endswith(sf) for sf in SIGUNGU_SUFFIXES):
        municipality = tokens[0]
        tokens = tokens[1:]

    if tokens and municipality and any(tokens[0].endswith(sf) for sf in ('읍', '면', '동')):
        emd = tokens[0]

    if not emd:
        emd_match = re.search(r'([가-힣]+(?:읍|면|동))', addr)
        if emd_match:
            emd = emd_match.group(1)

    return province, municipality, emd


# ═══════════════════════════════════════════════════════════
# 이름 / 주소 정규화
# ═══════════════════════════════════════════════════════════


def norm_name(name: str) -> str:
    """상호명 정규화: 특수문자 제거 + 소문자."""
    return re.sub(r'[^가-힣a-zA-Z0-9]', '', str(name or '')).lower()


def norm_addr(addr: str) -> str:
    """주소 정규화: 괄호 제거 + 시도 표준화 + 공백 제거 + 소문자."""
    cleaned = re.sub(r'\([^)]*\)', '', addr or '')
    toks = cleaned.split()
    if toks and toks[0] in SIDO_ALIAS:
        toks[0] = SIDO_ALIAS[toks[0]]
    return ''.join(toks).lower()
