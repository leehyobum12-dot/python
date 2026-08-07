# data_pipeline/adapters/safe_restaurant.py
import xml.etree.ElementTree as ET
from typing import List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from .base import BaseAdapter, CertRecord, get_session


class SafeRestaurantAdapter(BaseAdapter):
    """농식품부 안심식당 — ✅ 병렬 페이지 수집"""
    BASE_URL = "http://211.237.50.150:7080/openapi"
    SERVICE_ID = "Grid_20200713000000000605_1"
    PAGE_SIZE = 1000
    MAX_WORKERS = 8  # ✅ 8개 스레드 병렬

    def __init__(self, api_key: str):
        self.api_key = (api_key or "").strip()
        self.raw_rows = []

    def _fetch_page(self, start: int, end: int, province: Optional[str]):
        """단일 페이지 fetch (스레드 워커용)"""
        url = f"{self.BASE_URL}/{self.api_key}/xml/{self.SERVICE_ID}/{start}/{end}"
        params = {'RELAX_SI_NM': province} if province else {}
        session = get_session()
        
        try:
            res = session.get(url, params=params, timeout=30)
            if res.status_code != 200:
                return [], 0
            root = ET.fromstring(res.content)
            rows = root.findall('.//row')
            total = int(root.findtext('totalCnt') or 0)
            
            page_records = []
            for row in rows:
                raw = {c.tag: (c.text or '').strip() for c in row}
                use_yn = (row.findtext('RELAX_USE_YN') or '').strip()
                cancel_dt = (row.findtext('RELAX_RSTRNT_CNCL_DT') or '').strip()
                
                page_records.append(CertRecord(
                    source='mafra-safe', cert_type='safe_restaurant',
                    raw_name=(row.findtext('RELAX_RSTRNT_NM') or '').strip(),
                    raw_address=(row.findtext('RELAX_ADD1') or '').strip(),
                    province=(row.findtext('RELAX_SI_NM') or '').strip(),
                    municipality=(row.findtext('RELAX_SIDO_NM') or '').strip(),
                    phone=(row.findtext('RELAX_RSTRNT_TEL') or '').strip(),
                    biz_type=(row.findtext('RELAX_GUBUN_DETAIL') or '').strip(),
                    designated_at=(row.findtext('RELAX_RSTRNT_REG_DT') or '').strip(),
                    cancelled_at=cancel_dt or None,
                    is_active=(use_yn == 'Y' and not cancel_dt),
                    raw=raw,
                ))
            return page_records, total
        except Exception as e:
            print(f"   ⚠️ [safe_restaurant] page {start}-{end} 실패: {e}")
            return [], 0

    def collect(self, province: Optional[str] = None) -> List[CertRecord]:
        if not self.api_key:
            print("⚠️ [safe_restaurant] MAFRA_API_KEY 미설정 — 스킵")
            return []

        # ✅ 1단계: 첫 페이지로 전체 건수 파악
        first_page, total = self._fetch_page(1, self.PAGE_SIZE, province)
        if not first_page:
            return []
        
        all_records = list(first_page)
        self.raw_rows.extend([r.raw for r in first_page])
        
        if total <= self.PAGE_SIZE:
            valid = [r for r in all_records if r.is_valid()]
            print(f"✅ [safe_restaurant] {len(valid)}건 수집 (활성 필터 통과)")
            return valid
        
        # ✅ 2단계: 나머지 페이지를 병렬로 수집
        ranges = []
        start = self.PAGE_SIZE + 1
        while start <= total:
            end = min(start + self.PAGE_SIZE - 1, total)
            ranges.append((start, end))
            start = end + 1
        
        print(f"   🚀 [safe_restaurant] {len(ranges)}개 페이지 병렬 수집 시작...")
        
        with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as executor:
            futures = {
                executor.submit(self._fetch_page, s, e, province): (s, e)
                for s, e in ranges
            }
            for future in as_completed(futures):
                page_records, _ = future.result()
                all_records.extend(page_records)
                self.raw_rows.extend([r.raw for r in page_records])

        valid = [r for r in all_records if r.is_valid()]
        print(f"✅ [safe_restaurant] {len(valid)}건 수집 (활성 필터 통과)")
        return valid