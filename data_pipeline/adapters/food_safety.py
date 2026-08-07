# data_pipeline/adapters/food_safety.py
import os, json, requests, time
import xml.etree.ElementTree as ET
from typing import List, Optional
from .base import BaseAdapter, CertRecord, parse_addr_tokens, norm_name, norm_addr


class FoodSafetyAdapter(BaseAdapter):
    """
    식품안심업소(위생등급) 지정 현황 — 3중 폴백
      1순위: .env FOOD_SAFETY_URL (data.go.kr 정확한 URL)
      2순위: data.go.kr MFDS 기관(1471000) 후보 operation 자동 프로빙
      3순위: 식약처 원본 C004 (서버 필터 없음 → 클라이언트 필터)
    """
    DATAGO_ORG = "1471000"
    CANDIDATES = [
        ("FoodSafetyRstrn", "getFoodSafetyRstrn"),
        ("FoodSafetyRestaurant", "getFoodSafetyRestaurant"),
        ("HsncyStdrRstrn", "getHsncyStdrRstrn"),
        ("FoodSafetyGradeRstrn", "getFoodSafetyGradeRstrn"),
    ]
    PROBE_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               '..', 'out', 'food_safety_endpoint.json')
    PAGE_SIZE = 500
    TIMEOUT = 60
    MAX_RETRY = 5

    def __init__(self, service_key: str):
        self.service_key = (service_key or "").strip()
        self.raw_rows = []

    def _key(self):
        from urllib.parse import unquote
        return unquote(self.service_key) if '%' in self.service_key else self.service_key

    def _get(self, d, *keys):
        for k in keys:
            v = d.get(k)
            if v:
                return str(v).strip()
        return ''

    def _probe_datago(self) -> Optional[str]:
        if os.path.exists(self.PROBE_CACHE):
            try:
                with open(self.PROBE_CACHE, encoding='utf-8') as f:
                    url = json.load(f).get('url')
                if url:
                    return url
            except Exception:
                pass
        for svc, op in self.CANDIDATES:
            url = f"https://apis.data.go.kr/{self.DATAGO_ORG}/{svc}/{op}"
            try:
                res = requests.get(url, params={'serviceKey': self._key(),
                                   'pageNo': 1, 'numOfRows': 1, 'type': 'json'},
                                   timeout=self.TIMEOUT)
                if res.status_code == 200 and '"item"' in res.text:
                    with open(self.PROBE_CACHE, 'w', encoding='utf-8') as f:
                        json.dump({'url': url}, f)
                    print(f"✅ [food_safety] data.go.kr 엔드포인트 탐지: {svc}/{op}")
                    return url
            except Exception:
                continue
        return None

    def _fetch_page(self, kind, url, page, start, end):
        if kind == 'datago':
            res = requests.get(url, params={'serviceKey': self._key(), 'pageNo': page,
                               'numOfRows': self.PAGE_SIZE, 'type': 'json'},
                               timeout=self.TIMEOUT)
            if res.status_code != 200:
                return None
            body = res.json().get('response', {}).get('body', {})
            items = body.get('items', {})
            return items.get('item', []) if isinstance(items, dict) else (items or [])
        else:
            # mfds C004 — 재시도 로직 강화
            for attempt in range(self.MAX_RETRY):
                try:
                    res = requests.get(url.format(key=self._key(), start=start, end=end),
                                       timeout=self.TIMEOUT)
                    res.encoding = 'utf-8'
                    if res.status_code != 200:
                        continue
                    payload = res.json().get('C004', {})
                    result = payload.get('RESULT')
                    code = ''
                    if isinstance(result, list) and result:
                        code = (result[0] or {}).get('CODE', '')
                    elif isinstance(result, dict):
                        code = result.get('CODE', '')
                    if code not in (None, '', 'INFO-000'):
                        return None
                    rows = payload.get('row', [])
                    return [rows] if isinstance(rows, dict) else rows
                except Exception as e:
                    if attempt < self.MAX_RETRY - 1:
                        print(f"   ↻ [food_safety] mfds 재시도 {attempt+1}/{self.MAX_RETRY}: {e}")
                        time.sleep(5 * (attempt + 1))
                        continue
                    raise
            return None

    def collect(self, province: Optional[str] = None) -> List[CertRecord]:
        if not self.service_key:
            print("⚠️ [food_safety] 키 미설정 — 스킵")
            return []

        env_url = os.getenv('FOOD_SAFETY_URL', '').strip()
        strategies = []
        if env_url:
            strategies.append(('datago', env_url))
        probed = self._probe_datago()
        if probed and probed != env_url:
            strategies.append(('datago', probed))
        strategies.append(('mfds',
            "http://openapi.foodsafetykorea.go.kr/api/{key}/C004/json/{start}/{end}"))

        for kind, url in strategies:
            records, page, start = [], 1, 1
            while True:
                end = start + self.PAGE_SIZE - 1
                try:
                    items = self._fetch_page(kind, url, page, start, end)
                except Exception as e:
                    print(f"⚠️ [food_safety] {kind} page={page}: {e}")
                    print(f"   [food_safety] 중단됨 — 재실행 시 FOOD_SAFETY_RESUME_PAGE={page} 설정")
                    break
                if not items:
                    break

                for item in items:
                    self.raw_rows.append(item)

                    # ✅ 주소 파싱 (다중 필드명 방어)
                    addr = self._get(item, 'ADDR', 'SITE_ADDR', 'ADDR_NAME',
                                     '소재지도로명주소', 'RDNMADR')
                    if not addr:
                        continue
                    if province and not addr.startswith(province):
                        continue

                    p, m, e = parse_addr_tokens(addr)

                    # ✅ 상세 주소 필드 (있는 경우만 추출)
                    road_name = self._get(item, 'RDNMADR', 'ROAD_NM_ADDR', '도로명주소') or addr
                    lot_num = self._get(item, 'JIBUN_ADDR', 'LOT_ADDR', '지번주소') or ''

                    # ✅ 메뉴 (있는 경우만)
                    main_menu = self._get(item, 'MAIN_MENU', 'MENU_NM', '대표메뉴', '주메뉴')

                    clsbiz_dt = self._get(item, 'CLSBIZ_DT')
                    cancel_dt = self._get(item, 'ASGN_CANCEL_YMD')
                    status = self._get(item, 'CLSBIZ_DVS_CD_NM')

                    records.append(CertRecord(
                        source='mfds-food-safety',
                        cert_type='food_safety',
                        raw_name=self._get(item, 'BSSH_NM', 'UPSO_NM', '업소명'),
                        raw_address=addr,
                        province=p,
                        municipality=m,
                        emd=e,
                        road_name=road_name,
                        lot_num=lot_num,
                        main_menu=main_menu,
                        phone=self._get(item, 'TELNO'),
                        biz_type=self._get(item, 'INDUTY_NM'),
                        designated_at=self._get(item, 'ASGN_FROM'),
                        cancelled_at=cancel_dt or None,
                        closed_at=clsbiz_dt or None,
                        is_active=('폐업' not in status and not clsbiz_dt and not cancel_dt),
                        raw=item,
                    ))

                if len(items) < self.PAGE_SIZE:
                    break
                page += 1
                start = end + 1
                time.sleep(0.4)

                # 진행 상황 로깅 (1000건마다)
                if start > 1 and (start - 1) % 5000 == 0:
                    print(f"   [food_safety] 진행: {start-1}건 스캔, {len(records)}건 필터 통과")

            if records or self.raw_rows:
                print(f"✅ [food_safety] {kind} 전략 성공 — {len(records)}건 (원본 {len(self.raw_rows)}건)")
                return [r for r in records if r.is_valid()]

        print("🚨 [food_safety] 모든 전략 실패 — .env FOOD_SAFETY_URL 확인 필요")
        return []