# data_pipeline/adapters/low_sodium.py
import requests, time
from urllib.parse import unquote
from typing import List, Optional
from .base import BaseAdapter, CertRecord, parse_addr_tokens, match_prefixes
from concurrent.futures import ThreadPoolExecutor, as_completed


class LowSodiumAdapter(BaseAdapter):
    """식약처 나트륨 줄이기 실천음식점 (data.go.kr REST)"""
    BASE_URL = "https://apis.data.go.kr/1471000/NaReduceRstrtDsgnBsshRgstr"
    OPERATION = "/getNaReduceRstrtDsgnBsshRgstr"
    MAX_WORKERS = 6
    PAGE_SIZE = 500

    def __init__(self, service_key: str):
        self.service_key = (service_key or "").strip()
        self.raw_rows = []

    def _key(self) -> str:
        return unquote(self.service_key) if '%' in self.service_key else self.service_key

    def _items_of(self, data):
        body = (data or {}).get('body', {})
        items = body.get('items', [])
        if isinstance(items, dict):
            items = items.get('item', [])
        if not isinstance(items, list):
            items = [items] if items else []
        unwrapped = []
        for it in items:
            if isinstance(it, dict) and 'item' in it and len(it.keys()) == 1:
                unwrapped.append(it['item'])
            else:
                unwrapped.append(it)
        return unwrapped

    def _fetch_page(self, page: int, province_prefixes: tuple):
        """단일 페이지 fetch (스레드 워커용)"""
        try:
            res = requests.get(
                f"{self.BASE_URL}{self.OPERATION}",
                params={'serviceKey': self._key(), 'pageNo': page,
                        'numOfRows': self.PAGE_SIZE, 'type': 'json'},
                timeout=30,
            )
            if res.status_code != 200:
                return [], 0
            data = res.json()
            items = self._items_of(data)
            total = (data.get('body') or {}).get('totalCount', 0)

            records = []
            for item in items:
                self.raw_rows.append(item)   # ✅ raw_rows 채우기 (archive_raw용)
                addr = (item.get('SITE_ADDR') or '').strip()
                if province_prefixes and not addr.startswith(province_prefixes):
                    continue
                p, m, e = parse_addr_tokens(addr)
                records.append(CertRecord(
                    source='mfds-sodium', cert_type='low_sodium',
                    raw_name=(item.get('BSSH_NM') or '').strip(),
                    raw_address=addr, province=p, municipality=m,
                    phone=None,
                    biz_type=(item.get('INDUTY_CD_NM') or '').strip(),
                    designated_at=(item.get('JOIN_YMD') or item.get('APPT_YMD') or '').strip(),
                    is_active=True, raw=item,
                ))
            return records, total
        except Exception as e:
            print(f"   ⚠️ [low_sodium] page={page} 실패: {e}")
            return [], 0

    def collect(self, province=None):   # ✅ 들여쓰기 수정 (기존: 5칸 → 4칸)
        if not self.service_key:
            print("⚠️ [low_sodium] DATA_GO_KR_KEY 미설정 — 스킵")
            return []

        prefixes = match_prefixes(province) if province else None

        # ✅ 1단계: 첫 페이지로 전체 건수 파악
        first_records, total = self._fetch_page(1, prefixes)
        if not first_records and total == 0:
            return []

        all_records = list(first_records)

        if total <= self.PAGE_SIZE:
            valid = [r for r in all_records if r.is_valid()]
            print(f"✅ [low_sodium] {len(valid)}건 수집 (활성 필터 통과)")
            return valid

        # ✅ 2단계: 나머지 페이지 병렬 처리
        pages = list(range(2, (total // self.PAGE_SIZE) + 2))
        print(f"   🚀 [low_sodium] {len(pages)}개 페이지 병렬 수집 시작...")

        with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as executor:
            futures = {
                executor.submit(self._fetch_page, p, prefixes): p
                for p in pages
            }
            for future in as_completed(futures):
                page_records, _ = future.result()
                all_records.extend(page_records)

        valid = [r for r in all_records if r.is_valid()]
        print(f"✅ [low_sodium] {len(valid)}건 수집 (활성 필터 통과)")
        return valid