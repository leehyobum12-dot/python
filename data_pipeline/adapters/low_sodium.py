# data_pipeline/adapters/low_sodium.py
import requests, time
from urllib.parse import unquote
from typing import List, Optional
from .base import BaseAdapter, CertRecord, parse_addr_tokens


class LowSodiumAdapter(BaseAdapter):
    """식약처 나트륨 줄이기 실천음식점 (data.go.kr REST)"""
    BASE_URL = "https://apis.data.go.kr/1471000/NaReduceRstrtDsgnBsshRgstr"
    OPERATION = "/getNaReduceRstrtDsgnBsshRgstr"
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

    def collect(self, province: Optional[str] = None) -> List[CertRecord]:
        if not self.service_key:
            print("⚠️ [low_sodium] DATA_GO_KR_KEY 미설정 — 스킵")
            return []

        records = []
        page = 1
        while True:
            params = {'serviceKey': self._key(), 'pageNo': page,
                      'numOfRows': self.PAGE_SIZE, 'type': 'json'}
            try:
                res = requests.get(f"{self.BASE_URL}{self.OPERATION}", params=params, timeout=30)
                if res.status_code != 200:
                    print(f"⚠️ [low_sodium] HTTP {res.status_code}: {res.text[:200]}")
                    break
                data = res.json()
                code = (data.get('header') or {}).get('resultCode', '')
                if page == 1:
                    total = (data.get('body') or {}).get('totalCount', 0)
                    print(f"📊 [low_sodium] resultCode={code}, 전체 {total}건")
                if code and code not in ('00', '000', 'INFO-000', 'NORMAL'):
                    break

                items = self._items_of(data)
                if not items:
                    break

                for item in items:
                    self.raw_rows.append(item)
                    addr = (item.get('SITE_ADDR') or '').strip()
                    if province:
                        keywords = [province]
                        if province == '제주특별자치도':
                            keywords.extend(['제주시', '서귀포시'])
                        if not any(kw in addr for kw in keywords):
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

                if len(items) < self.PAGE_SIZE:
                    break
                page += 1
                time.sleep(0.3)
            except Exception as e:
                print(f"⚠️ [low_sodium] page={page} 오류: {type(e).__name__}: {e}")
                break

        valid = [r for r in records if r.is_valid()]
        print(f"✅ [low_sodium] {len(valid)}건 수집 (활성 필터 통과)")
        return valid