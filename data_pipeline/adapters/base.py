# data_pipeline/adapters/base.py
import re
import requests
from requests.adapters import HTTPAdapter
from dataclasses import dataclass, field
from typing import Optional, List
from abc import ABC, abstractmethod


_session: Optional[requests.Session] = None


def get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        adapter = HTTPAdapter(
            pool_connections=20, pool_maxsize=50,
            max_retries=3, pool_block=False,
        )
        _session.mount('http://', adapter)
        _session.mount('https://', adapter)
    return _session


# ─────────────── 주소 판별 유틸리티 ───────────────
EMD_SUFFIXES = ('동', '읍', '면', '리', '가')
ROAD_SUFFIXES = ('로', '길', '대로')
SIGUNGU_SUFFIX = ('시', '군', '구')


def is_road_name(token: Optional[str]) -> bool:
    if not token:
        return False
    return any(token.endswith(sf) for sf in ROAD_SUFFIXES)


def is_emd_token(token: Optional[str]) -> bool:
    if not token:
        return False
    if is_road_name(token):
        return False
    return any(token.endswith(sf) for sf in EMD_SUFFIXES)


def extract_emd_from_addr(addr: str) -> str:
    if not addr:
        return ''
    s = re.sub(r'\([^)]*\)', '', addr)
    toks = s.split()
    if len(toks) < 3:
        return ''
    for tok in toks[2:]:
        if is_emd_token(tok) and not any(tok.endswith(sf) for sf in SIGUNGU_SUFFIX):
            return tok
    return ''


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
    if province == '전남광주통합특별시':
        return ('전남광주통합특별시', '광주광역시', '전라남도')
    return (province,)


def parse_addr_tokens(addr: str):
    t = (addr or '').split()
    province = canon_province(t[0]) if len(t) > 0 else None
    municipality = t[1] if len(t) > 1 else None
    emd = None
    if len(t) > 2:
        candidate = t[2]
        if is_emd_token(candidate) and not is_road_name(candidate):
            emd = candidate
    return (province, municipality, emd)


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
    main_menu_source: Optional[str] = None   # public_api|cross_source|naver_api|kakao_api
    menus: List[str] = field(default_factory=list)   # ✅ [신규] 추가 메뉴 배열
    menus_source: Optional[str] = None               # ✅ [신규] 메뉴 배열 출처
    ghost_suspicion_score: int = 0                   # ✅ [신규] 유령 의심 점수 (0~100)
    coord_confidence: Optional[str] = None           # ✅ [신규] high|medium|low
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