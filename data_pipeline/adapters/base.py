# data_pipeline/adapters/base.py
from dataclasses import dataclass
from typing import Optional, List
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
import os
import re

# ✅ [v2.1] 글로벌 세션 풀 (Connection Pooling)
_session = None
def get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=20,      # 동시 연결 풀 크기
            pool_maxsize=50,          # 최대 연결 수
            max_retries=3,            # urllib3 수준 자동 재시도
            pool_block=False
        )
        _session.mount('http://', adapter)
        _session.mount('https://', adapter)
    return _session


# ✅ [v2.1] 병렬 처리 워커 풀
_executor = ThreadPoolExecutor(max_workers=10)  # I/O 바운드 → 10 워커


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


def parse_addr_tokens(addr: str):
    t = (addr or '').split()
    return (t[0] if len(t) > 0 else None,
            t[1] if len(t) > 1 else None,
            t[2] if len(t) > 2 else None)


def norm_name(name: str) -> str:
    return re.sub(r'[^가-힣a-zA-Z0-9]', '', str(name or '')).lower()


def norm_addr(addr: str) -> str:
    s = re.sub(r'\([^)]*\)', '', addr or '')
    return re.sub(r'[^가-힣a-zA-Z0-9]', '', s).lower()