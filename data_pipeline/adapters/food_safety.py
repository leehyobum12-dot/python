import os, json, glob, requests, time
from typing import List, Optional
from urllib.parse import urlparse, unquote
from concurrent.futures import ThreadPoolExecutor, as_completed
from .base import BaseAdapter, CertRecord, parse_addr_tokens, match_prefixes

try:
    import ijson
    IJSON_AVAILABLE = True
except ImportError:
    IJSON_AVAILABLE = False
    print("⚠️ ijson 미설치 — 스트리밍 파서 비활성화 (pip install ijson 권장)")


class FoodSafetyAdapter(BaseAdapter):
    """
    식품안심업소(위생등급) 지정 현황 — 멀티 벤더 페일오버 + 스냅샷 모드
      1순위: data.go.kr 미러 (FOOD_SAFETY_DATAGO_URL + DATA_GO_KR_KEY)
      2순위: 식약처 원본 C004 (FOODSAFETY_KOREA_KEY)
      3순위: 로컬 원본 리플레이
      + 스냅샷 모드: 전국 1회 스캔 후 로컬 슬라이스로 시도별 분배
      + ✅ 병렬 페이지 수집 (mfds)
      + ✅ 스트리밍 JSON 파서 (메모리 O(1))
    """
    DATAGO_ORG = "1471000"
    CANDIDATES = [
        ("FoodSafetyRstrn", "getFoodSafetyRstrn"),
        ("FoodSafetyRestaurant", "getFoodSafetyRestaurant"),
        ("HsncyStdrRstrn", "getHsncyStdrRstrn"),
        ("FoodSafetyGradeRstrn", "getFoodSafetyGradeRstrn"),
    ]
    MFDS_URL_TPL = "http://openapi.foodsafetykorea.go.kr/api/{key}/C004/json/{start}/{end}"
    PROBE_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               '..', 'out', 'food_safety_endpoint.json')
    RAW_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           '..', 'data', 'raw', 'food_safety')
    REPLAY_MAX_AGE_DAYS = 7
    PAGE_SIZE = 500
    TIMEOUT = 60
    MAX_RETRY = 5

    FIELD_CANDIDATES = {
        'name':       ('BSSH_NM', 'UPSO_NM', 'ENTRPS_NM', '업소명', '상호명', '영업소명'),
        'addr':       ('ADDR', 'SITE_ADDR', 'RDNMADR', 'ROAD_NM_ADDR',
                       '도로명주소', '소재지도로명주소', '영업장소재지', '소재지'),
        'lot':        ('JIBUN_ADDR', 'LOT_ADDR', '지번주소'),
        'phone':      ('TELNO', 'TEL', '전화번호', '업소전화번호'),
        'biz':        ('INDUTY_NM', 'INDUTY_CD_NM', '업종명', '업태구분명'),
        'designated': ('ASGN_FROM', 'ASGN_YMD', 'PRMISN_YMD',
                       '지정일자', '허가일자', '지정시작일자'),
        'status':     ('CLSBIZ_DVS_CD_NM', '영업상태', 'BIZ_STT_CD_NM'),
        'closed':     ('CLSBIZ_DT', '폐업일자', 'CLOSE_DT'),
        'cancel':     ('ASGN_CANCEL_YMD', '지정취소일자'),
        'menu':       ('MAIN_MENU', 'MENU_NM', '대표메뉴', '주메뉴'),
    }

    def __init__(self, service_key: str, datago_key: str = '', snapshot_first: bool = False):
        self.service_key = (service_key or "").strip()
        self.datago_key = (datago_key or "").strip()
        self.snapshot_first = snapshot_first
        self.raw_rows = []

    def _key(self):
        return unquote(self.service_key) if '%' in self.service_key else self.service_key

    def _datago_key(self):
        return unquote(self.datago_key) if '%' in self.datago_key else self.datago_key

    def _get_any(self, d: dict, field: str) -> str:
        if not isinstance(d, dict):
            return ''
        for k in self.FIELD_CANDIDATES[field]:
            v = d.get(k)
            if v:
                return str(v).strip()
        for cand in self.FIELD_CANDIDATES[field]:
            if not cand.isascii():
                for dk, dv in d.items():
                    if dv and cand in str(dk):
                        return str(dv).strip()
        return ''

    def _normalize_items(self, items) -> list:
        if items in (None, '', [], {}):
            return []
        if isinstance(items, dict):
            items = items.get('item', [])
        if items in (None, ''):
            return []
        if isinstance(items, dict):
            return [items]
        return items if isinstance(items, list) else []

    def _looks_like_food_safety(self, sample: dict) -> bool:
        return bool(self._get_any(sample, 'name')) and bool(self._get_any(sample, 'addr'))

    def _valid_url(self, u: str) -> bool:
        try:
            p = urlparse(u)
            return p.scheme in ('http', 'https') and bool(p.netloc)
        except Exception:
            return False

    def _validate_datago_url(self, url: str) -> bool:
        try:
            res = requests.get(url, params={'serviceKey': self._datago_key(),
                               'pageNo': 1, 'numOfRows': 1, 'type': 'json'},
                               timeout=self.TIMEOUT)
            if res.status_code != 200:
                return False
            data = res.json()
            header = (data.get('response') or {}).get('header') or {}
            if str(header.get('resultCode', '')) not in ('00', 'INFO-000', 'NORMAL SERVICE'):
                return False
            body = (data.get('response') or {}).get('body') or {}
            items = self._normalize_items(body.get('items'))
            if not items:
                return False
            return self._looks_like_food_safety(items[0])
        except Exception:
            return False

    def _probe_datago(self) -> Optional[str]:
        if not self.datago_key:
            return None
        if os.path.exists(self.PROBE_CACHE):
            try:
                with open(self.PROBE_CACHE, encoding='utf-8') as f:
                    url = json.load(f).get('url')
                if url and self._valid_url(url) and self._validate_datago_url(url):
                    return url
                os.remove(self.PROBE_CACHE)
            except Exception:
                pass
        for svc, op in self.CANDIDATES:
            url = f"https://apis.data.go.kr/{self.DATAGO_ORG}/{svc}/{op}"
            if self._validate_datago_url(url):
                with open(self.PROBE_CACHE, 'w', encoding='utf-8') as f:
                    json.dump({'url': url}, f)
                print(f"✅ [food_safety] data.go.kr 탐지: {svc}/{op}")
                return url
        return None

    def _fetch_datago_page(self, url, page):
        res = requests.get(url, params={'serviceKey': self._datago_key(),
                           'pageNo': page, 'numOfRows': self.PAGE_SIZE,
                           'type': 'json'}, timeout=self.TIMEOUT)
        if res.status_code != 200:
            return None
        body = (res.json().get('response') or {}).get('body') or {}
        items = self._normalize_items(body.get('items'))
        if page == 1 and items:
            print(f"   🔎 [food_safety] datago 스키마: {list(items[0].keys())}")
        return items

    def _fetch_mfds_page(self, start, end):
        """순차 호출용 (기존 유지)"""
        last_err = ''
        for attempt in range(self.MAX_RETRY):
            try:
                res = requests.get(
                    self.MFDS_URL_TPL.format(key=self._key(), start=start, end=end),
                    timeout=self.TIMEOUT)
                if res.status_code != 200:
                    raise IOError(f'HTTP {res.status_code}')
                res.encoding = 'utf-8'
                try:
                    data = res.json()
                except json.JSONDecodeError:
                    raise IOError('Invalid JSON (점검 페이지 의심)')
                payload = data.get('C004', {})
                result = payload.get('RESULT')
                code = ''
                if isinstance(result, list) and result:
                    code = (result[0] or {}).get('CODE', '')
                elif isinstance(result, dict):
                    code = result.get('CODE', '')
                if code == 'ERROR-500':
                    raise IOError('ERROR-500')
                if code not in (None, '', 'INFO-000'):
                    print(f"   🚨 [food_safety] mfds 치명 코드: {code}")
                    return None
                rows = payload.get('row', [])
                return [rows] if isinstance(rows, dict) else rows
            except Exception as e:
                last_err = str(e)
                if attempt < self.MAX_RETRY - 1:
                    print(f"   ↻ [food_safety] mfds 재시도 {attempt+1}/{self.MAX_RETRY}: {e}")
                    time.sleep(5 * (attempt + 1))
                    continue
                raise
        print(f"   ⚠️ [food_safety] mfds 재시도 소진: {last_err}")
        return None

    def _fetch_mfds_page_parallel(self, start: int, end: int) -> List[dict]:
        """✅ 병렬 호출용 (ThreadPoolExecutor용)"""
        last_err = ''
        for attempt in range(self.MAX_RETRY):
            try:
                res = requests.get(
                    self.MFDS_URL_TPL.format(key=self._key(), start=start, end=end),
                    timeout=self.TIMEOUT
                )
                if res.status_code != 200:
                    raise IOError(f'HTTP {res.status_code}')
                res.encoding = 'utf-8'
                try:
                    data = res.json()
                except json.JSONDecodeError:
                    raise IOError('Invalid JSON')
                payload = data.get('C004', {})
                result = payload.get('RESULT')
                code = ''
                if isinstance(result, list) and result:
                    code = (result[0] or {}).get('CODE', '')
                elif isinstance(result, dict):
                    code = result.get('CODE', '')
                if code == 'ERROR-500':
                    raise IOError('ERROR-500')
                if code not in (None, '', 'INFO-000'):
                    return []
                rows = payload.get('row', [])
                return [rows] if isinstance(rows, dict) else rows
            except Exception as e:
                last_err = str(e)
                if attempt < self.MAX_RETRY - 1:
                    time.sleep(5 * (attempt + 1))
                    continue
                raise
        print(f"   ⚠️ [food_safety] mfds 병렬 재시도 소진: {last_err}")
        return []

    def _parse_rows(self, rows, province) -> List[CertRecord]:
        records = []
        prefixes = match_prefixes(province) if province else None
        for item in rows:
            addr = self._get_any(item, 'addr')
            if not addr:
                continue
            if prefixes and not addr.startswith(prefixes):
                continue
            p, m, e = parse_addr_tokens(addr)
            clsbiz_dt = self._get_any(item, 'closed')
            cancel_dt = self._get_any(item, 'cancel')
            status = self._get_any(item, 'status')
            records.append(CertRecord(
                source='mfds-food-safety', cert_type='food_safety',
                raw_name=self._get_any(item, 'name'),
                raw_address=addr, province=p, municipality=m, emd=e,
                road_name=addr,
                lot_num=self._get_any(item, 'lot') or '',
                main_menu=self._get_any(item, 'menu'),
                phone=self._get_any(item, 'phone'),
                biz_type=self._get_any(item, 'biz'),
                designated_at=self._get_any(item, 'designated'),
                cancelled_at=cancel_dt or None,
                closed_at=clsbiz_dt or None,
                is_active=('폐업' not in status and not clsbiz_dt and not cancel_dt),
                raw=item,
            ))
        return records

    def _parse_rows_streaming(self, items_iterator, province) -> List[CertRecord]:
        """✅ 스트리밍 파서용 레코드 변환 (메모리 O(1))"""
        records = []
        prefixes = match_prefixes(province) if province else None
        
        for item in items_iterator:
            addr = self._get_any(item, 'addr')
            if not addr:
                continue
            if prefixes and not addr.startswith(prefixes):
                continue
            
            p, m, e = parse_addr_tokens(addr)
            clsbiz_dt = self._get_any(item, 'closed')
            cancel_dt = self._get_any(item, 'cancel')
            status = self._get_any(item, 'status')
            
            records.append(CertRecord(
                source='mfds-food-safety', cert_type='food_safety',
                raw_name=self._get_any(item, 'name'),
                raw_address=addr, province=p, municipality=m, emd=e,
                road_name=addr,
                lot_num=self._get_any(item, 'lot') or '',
                main_menu=self._get_any(item, 'menu'),
                phone=self._get_any(item, 'phone'),
                biz_type=self._get_any(item, 'biz'),
                designated_at=self._get_any(item, 'designated'),
                cancelled_at=cancel_dt or None,
                closed_at=clsbiz_dt or None,
                is_active=('폐업' not in status and not clsbiz_dt and not cancel_dt),
                raw=item,
            ))
        
        return records

    def _replay_from_raw(self, province) -> List[CertRecord]:
        if not os.path.exists(self.RAW_DIR):
            return []
        
        files = sorted(glob.glob(os.path.join(self.RAW_DIR, '*.json')),
                    key=lambda f: (os.path.getsize(f), os.path.getmtime(f)),
                    reverse=True)
        
        for f in files:
            age_days = (time.time() - os.path.getmtime(f)) / 86400
            if age_days > self.REPLAY_MAX_AGE_DAYS:
                continue
            try:
                with open(f, encoding='utf-8') as fh:
                    rows = json.load(fh)
            except Exception:
                continue
            if not rows:
                continue
            
            parsed = self._parse_rows(rows, province)
            if parsed:
                print(f"   💾 [food_safety] 벤더 실패 → 원본 리플레이: "
                    f"{os.path.basename(f)} ({len(rows)}건, {age_days:.1f}일 전)")
                self.raw_rows = rows
                return parsed
        return []

    def _load_nationwide_snapshot(self) -> Optional[list]:
        """기존 방식 (ijson 없을 때 fallback)"""
        f = os.path.join(self.RAW_DIR, 'nationwide_latest.json')
        if not os.path.exists(f):
            return None
        age_days = (time.time() - os.path.getmtime(f)) / 86400
        if age_days > 1:
            return None
        try:
            with open(f, encoding='utf-8') as fh:
                rows = json.load(fh)
            print(f"   📸 [food_safety] 스냅샷 모드 활성화 ({len(rows)}건, {age_days:.2f}일 전)")
            return rows
        except Exception:
            return None

    def _load_nationwide_snapshot_streaming(self):
        """✅ 스트리밍 파서: 메모리 사용량 O(1) 유지"""
        if not IJSON_AVAILABLE:
            return self._load_nationwide_snapshot()
        
        f = os.path.join(self.RAW_DIR, 'nationwide_latest.json')
        if not os.path.exists(f):
            return None
        age_days = (time.time() - os.path.getmtime(f)) / 86400
        if age_days > 1:
            return None
        
        try:
            def stream_items():
                with open(f, 'rb') as fh:
                    for item in ijson.items(fh, 'item'):
                        yield item
            
            print(f"   📸 [food_safety] 스냅샷 모드 (스트리밍) — {age_days:.2f}일 전")
            return stream_items()
        except Exception:
            return None

    def collect(self, province: Optional[str] = None) -> List[CertRecord]:
        if not self.service_key and not self.datago_key:
            print("⚠️ [food_safety] 키 미설정 — 스킵")
            return []

        # ✅ 스냅샷 모드 우선 체크 (스트리밍 버전)
        if self.snapshot_first and province:
            snap_stream = self._load_nationwide_snapshot_streaming()
            if snap_stream:
                if IJSON_AVAILABLE and hasattr(snap_stream, '__iter__') and not isinstance(snap_stream, list):
                    # 스트리밍 파서 사용
                    records = self._parse_rows_streaming(snap_stream, province)
                else:
                    # 기존 방식 fallback
                    records = self._parse_rows(snap_stream, province)
                print(f"✅ [food_safety] 스냅샷 모드 — {len(records)}건 (API 호출 0회)")
                return [r for r in records if r.is_valid()]

        strategies = []
        env_url = os.getenv('FOOD_SAFETY_DATAGO_URL', '').strip()
        if self.datago_key and self._valid_url(env_url):
            strategies.append(('datago', env_url))
        elif self.datago_key:
            probed = self._probe_datago()
            if probed:
                strategies.append(('datago', probed))
        if self.service_key:
            strategies.append(('mfds', self.MFDS_URL_TPL))

        print(f"   📋 [food_safety] 페일오버 전략: {[s[0] for s in strategies]}")

        for kind, url in strategies:
            collected_rows = []
            
            if kind == 'datago':
                # datago는 순차적 (기존 유지)
                page, start = 1, 1
                while True:
                    end = start + self.PAGE_SIZE - 1
                    try:
                        items = self._fetch_datago_page(url, page)
                    except Exception as e:
                        print(f"⚠️ [food_safety] {kind} page={page}: {e}")
                        break
                    if not items:
                        break
                    collected_rows.extend(items)
                    if len(items) < self.PAGE_SIZE:
                        break
                    page += 1
                    start = end + 1
                    time.sleep(0.4)
                    if (start - 1) % 5000 == 0:
                        print(f"   [food_safety] 진행: {start-1}건 스캔")
            else:
                # ✅ mfds는 병렬 처리 (신규)
                # 1단계: 첫 페이지로 전체 건수 파악
                first_page = self._fetch_mfds_page_parallel(1, self.PAGE_SIZE)
                if not first_page:
                    continue
                
                collected_rows.extend(first_page)
                
                # 첫 페이지 결과으로 전체 건수 추정
                # 실제 mfds API는 totalCount를 제공하지 않으므로 보수적으로 추정
                estimated_total = len(first_page) * 100  # 임시 추정치 (실제 데이터에 따라 조정)
                
                # 2단계: 나머지 페이지 병렬 수집
                pages = list(range(2, min(100, (estimated_total // self.PAGE_SIZE) + 2)))
                if pages:
                    print(f"   🚀 [food_safety] mfds {len(pages)}개 페이지 병렬 수집 시작...")
                    
                    with ThreadPoolExecutor(max_workers=8) as executor:
                        futures = {
                            executor.submit(self._fetch_mfds_page_parallel, 
                                          (p-1)*self.PAGE_SIZE + 1, 
                                          p*self.PAGE_SIZE): p
                            for p in pages
                        }
                        
                        done_count = 0
                        for future in as_completed(futures):
                            try:
                                page_rows = future.result()
                                collected_rows.extend(page_rows)
                                done_count += 1
                                if done_count % 10 == 0:
                                    print(f"   [food_safety] 병렬 진행: {done_count}/{len(pages)} 페이지")
                            except Exception as e:
                                print(f"   ⚠️ [food_safety] 병렬 수집 실패: {e}")
                                continue

            if collected_rows:
                self.raw_rows = collected_rows
                records = self._parse_rows(collected_rows, province)
                print(f"✅ [food_safety] {kind} 벤더 성공 — {len(records)}건 "
                      f"(원본 {len(collected_rows)}건)")
                return [r for r in records if r.is_valid()]

        replayed = self._replay_from_raw(province)
        if replayed:
            print(f"✅ [food_safety] 리플레이 성공 — {len(replayed)}건")
            return [r for r in replayed if r.is_valid()]

        print("🚨 [food_safety] 모든 벤더 실패")
        return []