# data_pipeline/adapters/base.py
import re
import requests
from dataclasses import dataclass
from typing import Optional, List
from abc import ABC, abstractmethod


# ─────────────── 전역 세션 풀 (Connection Pooling) ───────────────
_session: Optional[requests.Session] = None


def get_session() -> requests.Session:
    """TCP 핸드셰이크 재사용을 위한 싱글톤 세션"""
    global _session
    if _session is None:
        _session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=20,      # 유지할 커넥션 풀
            pool_maxsize=50,          # 최대 동시 커넥션
            max_retries=3,            # urllib3 레벨 자동 재시도
            pool_block=False,
        )
        _session.mount('http://', adapter)
        _session.mount('https://', adapter)
    return _session


# ─────────────── 행정구역 정규화 (2026.7.1 통폐합 대응) ───────────────
PROVINCE_CANON = {
    '광주광역시': '전남광주통합특별시',
    '전라남도': '전남광주통합특별시',
    '전남광주통합특별시': '전남광주통합특별시',
}
LEGACY_SPLIT = {
    '전남광주통합특별시': ['전라남도', '광주광역시'],
}
SIDO_ALIAS = {
    '서울': '서울특별시', '서울시': '서울특별시',
    '부산': '부산광역시', '부산시': '부산광역시',
    '대구': '대구광역시', '대구시': '대구광역시',
    '인천': '인천광역시', '인천시': '인천광역시',
    '대전': '대전광역시', '대전시': '대전광역시',
    '울산': '울산광역시', '울산시': '울산광역시',
    '세종': '세종특별자치시', '세종시': '세종특별자치시',
    '제주': '제주특별자치도', '제주도': '제주특별자치도',
    '경기': '경기도', '강원': '강원특별자치도',
    '충북': '충청북도', '충남': '충청남도',
    '전북': '전북특별자치도', '경북': '경상북도', '경남': '경상남도',
}


def canon_province(p: Optional[str]) -> Optional[str]:
    return PROVINCE_CANON.get(p, p) if p else p


def match_prefixes(province: str) -> tuple:
    """startswith/contains용 튜플 (신구 명칭 모두 매칭)"""
    if province == '전남광주통합특별시':
        return ('전남광주통합특별시', '광주광역시', '전라남도')
    return (province,)


def parse_addr_tokens(addr: str):
    """주소 토큰 → (시도[정규화], 시군구, 읍면동)"""
    t = (addr or '').split()
    return (canon_province(t[0]) if len(t) > 0 else None,
            t[1] if len(t) > 1 else None,
            t[2] if len(t) > 2 else None)


def norm_name(name: str) -> str:
    return re.sub(r'[^가-힣a-zA-Z0-9]', '', str(name or '')).lower()


def norm_addr(addr: str) -> str:
    s = re.sub(r'\([^)]*\)', '', addr or '')
    toks = s.split()
    if toks and toks[0] in SIDO_ALIAS:
        toks[0] = SIDO_ALIAS[toks[0]]
    return ''.join(toks).lower()


@dataclass
class CertRecord:
    source: str
    cert_type: str
    raw_name: str
    raw_address: str
    province: Optional[str] = None
    municipality: Optional[str] = None
    emd: Optional[str] = None
    road_name: Optional[str] = None
    lot_num: Optional[str] = None
    phone: Optional[str] = None
    biz_type: Optional[str] = None
    main_menu: Optional[str] = None
    designated_at: Optional[str] = None
    cancelled_at: Optional[str] = None
    closed_at: Optional[str] = None
    is_active: bool = True
    lat: Optional[float] = None
    lng: Optional[float] = None
    cuisine_tags: Optional[list] = None
    nutrient_tags: Optional[list] = None
    cooking_tags: Optional[list] = None
    ingredient_tags: Optional[list] = None
    regional_tags: Optional[list] = None
    allergen_tags: Optional[list] = None
    occasion_tags: Optional[list] = None
    raw: Optional[dict] = None
    dedup_key: Optional[str] = None

    def is_valid(self) -> bool:
        return (self.is_active
                and not self.cancelled_at
                and not self.closed_at
                and bool(self.raw_name and self.raw_address))


class BaseAdapter(ABC):
    @abstractmethod
    def collect(self, province: Optional[str] = None) -> List[CertRecord]:
        pass