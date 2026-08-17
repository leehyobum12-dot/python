# data_pipeline/enrich/official_enricher.py
"""
공식 API 통합 Enrichment 모듈 (v2.0 Final - NAVER API HUB 대응)
- 메뉴: NAVER API HUB 3단계 (블로그 → 카페글 → 뉴스)
- 유령가게: NAVER API HUB 3중 검증
- 좌표: 네이버 지오코딩(NCP) + 카카오 로컬 상호 검증
- ✅ 강화된 패턴 매칭 + 후처리 검증
- ✅ NAVER API HUB 엔드포인트 + NCP 헤더
"""

import os
import json
import asyncio
import re
import httpx
from datetime import date, datetime
from typing import List, Dict, Optional, Tuple
from math import radians, cos, sin, asin, sqrt
try:
    from .ner.core.ner_pipeline import NERMenuPipeline
except ImportError:
    from ner.core.ner_pipeline import NERMenuPipeline
from adapters.base import CertRecord


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE_DIR, '..', 'out')

QUOTA_PATH = os.path.join(OUT_DIR, 'official_enrich_quota.json')
CHECKPOINT_PATH = os.path.join(OUT_DIR, 'official_enrich_progress.json')
ENRICH_CACHE_PATH = os.path.join(OUT_DIR, 'official_enrich_cache.json')

# ─────────────── 일일 쿼터 한도 ───────────────
DAILY_LIMITS = {
    'kakao': 20_000,
    'naver_blog': 8_000,
    'naver_cafe': 5_000,
    'naver_news': 3_000,
    'naver_geo': 2_000,
}

# ─────────────── ✅ NAVER API HUB 엔드포인트 ───────────────
NAVER_HUB_BASE = "https://naverapihub.apigw.ntruss.com"
NAVER_BLOG_URL = f"{NAVER_HUB_BASE}/search/v1/blog"
NAVER_NEWS_URL = f"{NAVER_HUB_BASE}/search/v1/news"
NAVER_CAFE_URL = f"{NAVER_HUB_BASE}/search/v1/cafearticle"

# 네이버 지오코딩 (NCP)
NAVER_GEO_URL = "https://maps.apigw.ntruss.com/map-geocode/v2/geocode"

# 카카오 로컬
KAKAO_LOCAL_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"


class QuotaExceededError(Exception):
    pass


class QuotaTracker:
    def __init__(self):
        self.state = self._load()
        self.ner_pipeline = NERMenuPipeline()

    def _load(self) -> Dict:
        today = date.today().isoformat()
        default = {
            'date': today, 'kakao': 0,
            'naver_blog': 0, 'naver_cafe': 0, 'naver_news': 0, 'naver_geo': 0,
        }
        if os.path.exists(QUOTA_PATH):
            try:
                with open(QUOTA_PATH, encoding='utf-8') as f:
                    state = json.load(f)
                if state.get('date') == today:
                    for key, val in default.items():
                        if key not in state:
                            state[key] = val
                    return state
            except Exception:
                pass
        return default

    def _save(self):
        os.makedirs(os.path.dirname(QUOTA_PATH), exist_ok=True)
        with open(QUOTA_PATH, 'w', encoding='utf-8') as f:
            json.dump(self.state, f, ensure_ascii=False)

    def check_and_consume(self, provider: str):
        used = self.state.get(provider, 0)
        limit = DAILY_LIMITS.get(provider, 0)
        if used >= limit:
            raise QuotaExceededError(f"{provider} 쿼터 소진 ({used}/{limit})")
        self.state[provider] = used + 1
        if self.state[provider] % 200 == 0:
            self._save()

    def finalize(self):
        self._save()

    def summary(self) -> str:
        return (f"카카오 {self.state.get('kakao', 0)}/{DAILY_LIMITS['kakao']}, "
                f"블로그 {self.state.get('naver_blog', 0)}/{DAILY_LIMITS['naver_blog']}, "
                f"카페 {self.state.get('naver_cafe', 0)}/{DAILY_LIMITS['naver_cafe']}, "
                f"뉴스 {self.state.get('naver_news', 0)}/{DAILY_LIMITS['naver_news']}, "
                f"지오 {self.state.get('naver_geo', 0)}/{DAILY_LIMITS['naver_geo']}")


class EnrichCache:
    def __init__(self):
        self.cache = self._load()

    def _load(self) -> Dict:
        if os.path.exists(ENRICH_CACHE_PATH):
            try:
                with open(ENRICH_CACHE_PATH, encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def save(self):
        os.makedirs(os.path.dirname(ENRICH_CACHE_PATH), exist_ok=True)
        with open(ENRICH_CACHE_PATH, 'w', encoding='utf-8') as f:
            json.dump(self.cache, f, ensure_ascii=False)

    def get(self, key: str) -> Optional[Dict]:
        return self.cache.get(key)

    def set(self, key: str, value: Dict):
        self.cache[key] = value


def _haversine(lat1, lng1, lat2, lng2):
    R = 6371000
    lat1, lng1, lat2, lng2 = map(radians, [lat1, lng1, lat2, lng2])
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    a = sin(dlat/2)**2 + cos(lat1)*cos(lat2)*sin(dlng/2)**2
    return R * 2 * asin(sqrt(a))


def _clean_html(text: str) -> str:
    return re.sub(r'<[^>]+>', '', text or '')


# ─────────────── ✅ 강화된 메뉴 추출 로직 ───────────────

# 블랙리스트: 메뉴가 아닌 단어 (실제 테스트에서 오매칭된 것들)
MENU_BLACKLIST = {
    '제주맛집', '아래와', '비교', '길에', '있다면', '완전', '정말', '진짜',
    '너무', '꼭', '강추', '추천', '드시길', '드셔보세요', '였고', '입니다',
    '했어요', '습니다', '습니다', '드립니다', '해요', '하네요', '같아요',
    '있습니다', '없습니다', '되었습니다', '됩니다', '먹었어요', '다녀왔',
}

# 조사/어미 패턴 (제거 대상)
POSTFIX_PATTERNS = [
    r'(입니다|이에요|예요|입니다|었습니다|었어요|습니다|해요|하네요|같아요)$',
    r'(를|을|는|은|이|가|에|에서|으로|까지|부터|만|도|라도|조차|마저)$',
]


def _clean_menu_text(text: str) -> str:
    """추출된 메뉴 텍스트 정리 (조사/어미 제거)"""
    text = text.strip()
    for pattern in POSTFIX_PATTERNS:
        text = re.sub(pattern, '', text)
    return text.strip()


def _is_valid_menu(menu: str, store_name: str) -> bool:
    """추출된 메뉴가 유효한지 검증"""
    if not menu or len(menu) < 2 or len(menu) > 15:
        return False
    
    # 블랙리스트 체크
    if menu in MENU_BLACKLIST:
        return False
    
    # 가게 이름과 너무 유사하면 제외 (예: "해장국" in "우진해장국")
    if store_name and menu in store_name:
        return False
    
    # 숫자만 있는 경우 제외
    if re.match(r'^\d+$', menu):
        return False
    
    # 특수문자 포함 제외
    if re.search(r'[^\uAC00-\uD7A3a-zA-Z0-9\s]', menu):
        return False
    
    return True


# 강화된 패턴 (더 정확하게)
MENU_PATTERNS = [
    # "대표메뉴: OO" 형식 (콜론/등호 뒤 단어만)
    (r'대표\s*메뉴\s*[:는]\s*([가-힣]{2,10})(?:\s|$|,)', '대표메뉴'),
    (r'추천\s*메뉴\s*[:는]\s*([가-힣]{2,10})(?:\s|$|,)', '추천메뉴'),
    (r'시그니처\s*[:는]\s*([가-힣]{2,10})(?:\s|$|,)', '시그니처'),
    
    # "OO 먹고/드셨" 패턴 (뒤에 조사 허용)
    (r'([가-힣]{2,8})(?:를|을)?\s*먹(?:고|었|는)', '먹고'),
    (r'([가-힣]{2,8})(?:를|을)?\s*드셨', '드셨'),
    
    # "OO 진짜/정말 맛있" 패턴
    (r'([가-힣]{2,8})\s*(?:진짜|정말|너무)\s*맛있', '맛있'),
    
    # 제주도 특화 메뉴 (고정값)
    (r'(흑돼지\s*구이|고기\s*국수|갈치\s*조림|성게\s*미역국|보말\s*칼국수|전복\s*죽|몸국|돔베고기)', '제주특화'),
    
    # 일반 메뉴 키워드 (뒤에 조사/어미 허용하되 제거)
    (r'([가-힣]{2,8}(?:구이|찌개|탕|국밥|비빔밥|냉면|회|초밥|전골|덮밥|해장국|국수|라면|파스타|피자|스테이크))', '일반메뉴'),
]


def extract_menu_with_validation(text: str, store_name: str) -> Tuple[Optional[str], List[str], Optional[str]]:
    """메뉴 추출 + 후처리 검증"""
    candidates = []
    
    for pattern, pattern_name in MENU_PATTERNS:
        matches = re.findall(pattern, text)
        for match in matches:
            # 후처리: 조사/어미 제거
            cleaned = _clean_menu_text(match)
            # 유효성 검증
            if _is_valid_menu(cleaned, store_name):
                candidates.append((cleaned, pattern_name))
    
    if not candidates:
        return None, [], None
    
    # 첫 번째 유효한 메뉴 선택
    main_menu, pattern_name = candidates[0]
    extra_menus = [m for m, _ in candidates[1:6] if m != main_menu]
    
    return main_menu, extra_menus, f"pattern_{pattern_name}"


class OfficialEnricher:
    def __init__(self):
        self.kakao_key = os.getenv('KAKAO_REST_API_KEY', '')
        self.naver_geo_id = os.getenv('NAVER_CLIENT_ID', '')
        self.naver_geo_secret = os.getenv('NAVER_CLIENT_SECRET', '')
        self.naver_search_id = os.getenv('NAVER_SEARCH_ID', '')
        self.naver_search_secret = os.getenv('NAVER_SEARCH_SECRET', '')

        print(f"🔑 OfficialEnricher 초기화:")
        print(f"   카카오 로컬: {'✅' if self.kakao_key else '❌'}")
        print(f"   네이버 지오코딩: {'✅' if self.naver_geo_id else '❌'}")
        print(f"   네이버 검색(API HUB): {'✅' if self.naver_search_id else '❌'}")

        self.quota = QuotaTracker()
        self.ecache = EnrichCache()
        self.stats = {
        # 패턴 매칭 기반
        'menu_blog': 0,
        'menu_cafe': 0,
        'menu_news': 0,
        'menu_none': 0,
        # ✅ NER 기반 (신규 추가 - 이 부분이 필수!)
        'menu_blog_ner': 0,
        'menu_cafe_ner': 0,
        'menu_news_ner': 0,
        # 유령/좌표
        'ghost_open': 0,
        'ghost_closed': 0,
        'ghost_unknown': 0,
        'coord_high': 0,
        'coord_medium': 0,
        'coord_low': 0,
        'skipped': 0,
        }
        
         # ✅ 수정: 타입 어노테이션 + try-except로 안전한 초기화
        self.ner_pipeline: Optional[NERMenuPipeline] = None
        try:
            self.ner_pipeline = NERMenuPipeline()
            print(f"   NER 파이프라인: ✅")
        except Exception as e:
            print(f"   NER 파이프라인: ❌ 초기화 실패 ({e})")
            print(f"   → 기존 패턴 매칭으로 fallback")

    def _load_checkpoint(self) -> Dict:
        if os.path.exists(CHECKPOINT_PATH):
            try:
                with open(CHECKPOINT_PATH, encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return {'processed_keys': []}

    def _save_checkpoint(self, cp: Dict):
        os.makedirs(os.path.dirname(CHECKPOINT_PATH), exist_ok=True)
        cp['updated_at'] = datetime.now().isoformat()
        with open(CHECKPOINT_PATH, 'w', encoding='utf-8') as f:
            json.dump(cp, f, ensure_ascii=False)

    # ─────────────── ✅ NAVER API HUB 호출 ───────────────
    async def _search_naver_hub(self, client: httpx.AsyncClient,
                                 api_type: str, query: str,
                                 display: int = 5) -> List[dict]:
        """NAVER API HUB 검색 (새 엔드포인트 + NCP 헤더)"""
        if not self.naver_search_id or not self.naver_search_secret:
            return []

        quota_key = f'naver_{api_type}'
        url_map = {
            'blog': NAVER_BLOG_URL,
            'news': NAVER_NEWS_URL,
            'cafe': NAVER_CAFE_URL,
        }

        url = url_map.get(api_type)
        if not url:
            return []

        try:
            res = await client.get(
                url,
                params={'query': query, 'display': display, 'format': 'json'},
                headers={
                    'X-NCP-APIGW-API-KEY-ID': self.naver_search_id,
                    'X-NCP-APIGW-API-KEY': self.naver_search_secret,
                },
                timeout=10,
            )
            
            if res.status_code == 200:
                self.quota.check_and_consume(quota_key)
                return res.json().get('items', [])
            elif res.status_code == 401:
                print(f"      ❌ [네이버 {api_type}] 401 인증 실패")
                return []
            else:
                return []
        except QuotaExceededError:
            raise
        except Exception:
            return []

    # ─────────────── 메뉴 enrichment (강화 버전) ───────────────
    async def _enrich_menu_full(self, client: httpx.AsyncClient,
                                 record: CertRecord) -> Tuple[Optional[str], List[str], Optional[str]]:
        """3단계 메뉴 enrichment (NER + 패턴 매칭 병행)"""
        
        # 1단계: 블로그
        blog_items = await self._search_naver_hub(client, 'blog', f'{record.raw_name} 메뉴 추천')
        for item in blog_items:
            text = _clean_html((item.get('title', '') or '') + ' ' + (item.get('description', '') or ''))
            
            # ✅ 수정: ner_pipeline이 None이 아닐 때만 사용
            if self.ner_pipeline is not None:
                ner_result = self.ner_pipeline.extract_menus_from_text(
                    text=text,
                    store_name=record.raw_name,
                    source='blog'
                )
                if ner_result['main_menu']:
                    return ner_result['main_menu'], ner_result['sub_menus'], 'naver_blog_ner'
            
            # NER 결과 없으면 기존 패턴 매칭
            menu, extras, source = extract_menu_with_validation(text, record.raw_name)
            if menu:
                return menu, extras, 'naver_blog'

        # 2단계: 카페글
        cafe_items = await self._search_naver_hub(client, 'cafe', f'{record.raw_name} 메뉴')
        for item in cafe_items:
            text = _clean_html((item.get('title', '') or '') + ' ' + (item.get('description', '') or ''))
            
            if self.ner_pipeline is not None:
                ner_result = self.ner_pipeline.extract_menus_from_text(
                    text=text,
                    store_name=record.raw_name,
                    source='cafe'
                )
                if ner_result['main_menu']:
                    return ner_result['main_menu'], ner_result['sub_menus'], 'naver_cafe_ner'
            
            menu, extras, source = extract_menu_with_validation(text, record.raw_name)
            if menu:
                return menu, extras, 'naver_cafe'

        # 3단계: 뉴스
        news_items = await self._search_naver_hub(client, 'news', f'{record.raw_name} 메뉴')
        for item in news_items:
            text = _clean_html((item.get('title', '') or '') + ' ' + (item.get('description', '') or ''))
            
            if self.ner_pipeline is not None:
                ner_result = self.ner_pipeline.extract_menus_from_text(
                    text=text,
                    store_name=record.raw_name,
                    source='news'
                )
                if ner_result['main_menu']:
                    return ner_result['main_menu'], ner_result['sub_menus'], 'naver_news_ner'
            
            menu, extras, source = extract_menu_with_validation(text, record.raw_name)
            if menu:
                return menu, extras, 'naver_news'

        return None, [], None

    # ─────────────── 유령가게 검증 ───────────────
    async def _verify_ghost_full(self, client: httpx.AsyncClient,
                                  record: CertRecord) -> int:
        score = 0
        now = datetime.now()

        # 뉴스 검증
        news_items = await self._search_naver_hub(client, 'news', f'{record.raw_name} 폐업', display=3)
        for item in news_items:
            text = _clean_html((item.get('title', '') or '') + ' ' + (item.get('description', '') or '')).lower()
            if any(kw in text for kw in ['폐업', '문을 닫', '없어졌', '사라졌']):
                score += 50
                break

        # 블로그 검증
        blog_items = await self._search_naver_hub(client, 'blog', f'{record.raw_name} 폐업', display=3)
        for item in blog_items:
            text = _clean_html((item.get('title', '') or '') + ' ' + (item.get('description', '') or '')).lower()
            if any(kw in text for kw in ['폐업', '문을 닫', '없어졌', '사라졌', '닫았']):
                score += 30
                break

        # 카페 검증
        cafe_items = await self._search_naver_hub(client, 'cafe', f'{record.raw_name} 폐업', display=3)
        for item in cafe_items:
            text = _clean_html((item.get('title', '') or '') + ' ' + (item.get('description', '') or '')).lower()
            if any(kw in text for kw in ['폐업', '문을 닫', '없어졌']):
                score += 20
                break

        # 영업중 신호
        recent_items = await self._search_naver_hub(client, 'blog', f'{record.raw_name} 맛집', display=3)
        for item in recent_items:
            text = _clean_html((item.get('title', '') or '') + ' ' + (item.get('description', '') or '')).lower()
            if any(kw in text for kw in ['다녀왔', '방문', '맛있', '추천', '가봤', '재방문']):
                postdate = item.get('postdate', '')
                if postdate:
                    try:
                        pd = datetime.strptime(postdate, '%Y%m%d')
                        days_ago = (now - pd).days
                        if days_ago <= 365:
                            score -= 30
                            break
                    except Exception:
                        pass

        return max(0, min(100, score))

    # ─────────────── 좌표 복원 ───────────────
    async def _fetch_naver_geo(self, client: httpx.AsyncClient,
                                address: str) -> Optional[Tuple[float, float]]:
        if not self.naver_geo_id or not self.naver_geo_secret or not address:
            return None
        try:
            self.quota.check_and_consume('naver_geo')
            res = await client.get(
                NAVER_GEO_URL,
                params={'query': address},
                headers={
                    'X-NCP-APIGW-API-KEY-ID': self.naver_geo_id,
                    'X-NCP-APIGW-API-KEY': self.naver_geo_secret,
                    'Referer': 'http://localhost',
                },
                timeout=10,
            )
            if res.status_code == 200:
                addrs = res.json().get('addresses', [])
                if addrs:
                    try:
                        lat = float(addrs[0].get('y', 0))
                        lng = float(addrs[0].get('x', 0))
                        if 33.0 <= lat <= 38.6 and 124.5 <= lng <= 132.0:
                            return (lat, lng)
                    except Exception:
                        pass
        except QuotaExceededError:
            raise
        except Exception:
            pass
        return None

    async def _fetch_kakao_local(self, client: httpx.AsyncClient,
                                  address: str) -> Optional[Tuple[float, float]]:
        if not self.kakao_key or not address:
            return None
        try:
            self.quota.check_and_consume('kakao')
            res = await client.get(
                KAKAO_LOCAL_URL,
                params={'query': address, 'size': 1},
                headers={'Authorization': f'KakaoAK {self.kakao_key}'},
                timeout=10,
            )
            if res.status_code == 200:
                docs = res.json().get('documents', [])
                if docs:
                    try:
                        lat = float(docs[0].get('y', 0))
                        lng = float(docs[0].get('x', 0))
                        if 33.0 <= lat <= 38.6 and 124.5 <= lng <= 132.0:
                            return (lat, lng)
                    except Exception:
                        pass
        except QuotaExceededError:
            raise
        except Exception:
            pass
        return None

    async def _fix_coordinate(self, client: httpx.AsyncClient,
                               record: CertRecord) -> Tuple[Optional[float], Optional[float], Optional[str]]:
        naver_task = self._fetch_naver_geo(client, record.raw_address)
        kakao_task = self._fetch_kakao_local(client, record.raw_address)

        naver_res, kakao_res = await asyncio.gather(
            naver_task, kakao_task, return_exceptions=True
        )

        if isinstance(naver_res, QuotaExceededError):
            raise naver_res
        if isinstance(kakao_res, QuotaExceededError):
            raise kakao_res

        naver_lat: Optional[float] = None
        naver_lng: Optional[float] = None
        kakao_lat: Optional[float] = None
        kakao_lng: Optional[float] = None

        if isinstance(naver_res, tuple) and len(naver_res) == 2:
            naver_lat, naver_lng = naver_res
        if isinstance(kakao_res, tuple) and len(kakao_res) == 2:
            kakao_lat, kakao_lng = kakao_res

        if naver_lat and naver_lng and kakao_lat and kakao_lng:
            dist = _haversine(naver_lat, naver_lng, kakao_lat, kakao_lng)
            if dist < 100:
                return naver_lat, naver_lng, 'high'
            else:
                return naver_lat, naver_lng, 'medium'
        elif naver_lat and naver_lng:
            return naver_lat, naver_lng, 'medium'
        elif kakao_lat and kakao_lng:
            return kakao_lat, kakao_lng, 'medium'
        return None, None, 'low'

    # ─────────────── 메인 배치 처리 ───────────────
    async def _process_batch_async(self, records: List[CertRecord]):
        cp = self._load_checkpoint()
        processed_set = set(cp['processed_keys'])

        async with httpx.AsyncClient() as client:
            total = len(records)
            for idx, record in enumerate(records, 1):
                if record.dedup_key in processed_set:
                    self.stats['skipped'] += 1
                    continue

                try:
                    # 메뉴 enrichment
                    if not record.main_menu:
                        cache_key = f"menu|{record.dedup_key}"
                        cached = self.ecache.get(cache_key)

                        if cached is not None:
                            if cached.get('menu'):
                                record.main_menu = cached['menu']
                                source = cached.get('source')
                                
                                # ✅ 수정 1: 캐시에서 로드 시
                                if source and isinstance(source, str) and source.startswith('naver_'):
                                    source_key = source[len('naver_'):]
                                else:
                                    source_key = 'none'
                                
                                record.main_menu_source = source or 'naver_blog'
                                record.menus = cached.get('menus', [])
                                record.menus_source = source or 'naver_blog'
                                stat_key = f"menu_{source_key}"
                                self.stats.setdefault(stat_key, 0)
                                self.stats[stat_key] += 1
                            else:
                                self.stats['menu_none'] += 1
                        else:
                            menu, extras, source = await self._enrich_menu_full(client, record)
                            if menu:
                                record.main_menu = menu
                                record.main_menu_source = source
                                record.menus = extras
                                record.menus_source = source
                                
                                # ✅ 수정 2: 새로 추출 시
                                if source and isinstance(source, str) and source.startswith('naver_'):
                                    source_key = source[len('naver_'):]
                                else:
                                    source_key = 'none'
                                
                                stat_key = f"menu_{source_key}"
                                self.stats.setdefault(stat_key, 0)
                                self.stats[stat_key] += 1
                            else:
                                self.stats['menu_none'] += 1
                            self.ecache.set(cache_key, {
                                'menu': menu, 'menus': extras, 'source': source,
                            })

                    # 유령가게 검증
                    if not record.is_active:
                        score = await self._verify_ghost_full(client, record)
                        record.ghost_suspicion_score = score
                        if score <= -20:
                            record.is_active = True
                            self.stats['ghost_open'] += 1
                        elif score >= 30:
                            self.stats['ghost_closed'] += 1
                        else:
                            self.stats['ghost_unknown'] += 1

                    # 좌표 복원
                    if not record.lat or not record.lng:
                        lat, lng, conf = await self._fix_coordinate(client, record)
                        if lat and lng:
                            record.lat = lat
                            record.lng = lng
                        record.coord_confidence = conf
                        if conf == 'high':
                            self.stats['coord_high'] += 1
                        elif conf == 'medium':
                            self.stats['coord_medium'] += 1
                        else:
                            self.stats['coord_low'] += 1

                except QuotaExceededError as e:
                    print(f"\n🛑 {e} — 체크포인트 저장 후 중단")
                    break

                processed_set.add(record.dedup_key)
                if idx % 100 == 0:
                    print(f"   진행: {idx}/{total} "
                          f"(메뉴 {self.stats['menu_blog'] + self.stats['menu_cafe'] + self.stats['menu_news']}, "
                          f"유령복구 {self.stats['ghost_open']}, "
                          f"좌표복원 {self.stats['coord_high'] + self.stats['coord_medium']})")
                    self._save_checkpoint({'processed_keys': list(processed_set)})
                    self.quota.finalize()
                    self.ecache.save()

                await asyncio.sleep(0.1)

        self._save_checkpoint({'processed_keys': list(processed_set)})
        self.quota.finalize()
        self.ecache.save()

    def enrich_batch(self, records: List[CertRecord]) -> List[CertRecord]:
        if not self.kakao_key and not self.naver_search_id:
            print("⚠️ KAKAO/NAVER_SEARCH 키 미설정 — 공식 enrichment 스킵")
            return records

        menu_missing = sum(1 for r in records if not r.main_menu)
        ghost_candidates = sum(1 for r in records if not r.is_active)
        coord_missing = sum(1 for r in records if not r.lat or not r.lng)

        print(f"🔍 공식 API Enrichment 대상:")
        print(f"   메뉴 없음: {menu_missing}건")
        print(f"   유령 의심: {ghost_candidates}건")
        print(f"   좌표 없음: {coord_missing}건")

        if not records:
            return records

        asyncio.run(self._process_batch_async(records))

        print(f"\n✅ 공식 API Enrichment 완료:")
        print(f"   메뉴 발견 (블로그 패턴): {self.stats['menu_blog']}건")
        print(f"   메뉴 발견 (블로그 NER): {self.stats['menu_blog_ner']}건")
        print(f"   메뉴 발견 (카페 패턴): {self.stats['menu_cafe']}건")
        print(f"   메뉴 발견 (카페 NER): {self.stats['menu_cafe_ner']}건")
        print(f"   메뉴 발견 (뉴스 패턴): {self.stats['menu_news']}건")
        print(f"   메뉴 발견 (뉴스 NER): {self.stats['menu_news_ner']}건")
        print(f"   메뉴 없음 (추측 금지): {self.stats['menu_none']}건")
        print(f"   유령 영업 확인 (복구): {self.stats['ghost_open']}건")
        print(f"   유령 폐업 확정: {self.stats['ghost_closed']}건")
        print(f"   유령 판단 불가: {self.stats['ghost_unknown']}건")
        print(f"   좌표 고신뢰: {self.stats['coord_high']}건")
        print(f"   좌표 중신뢰: {self.stats['coord_medium']}건")
        print(f"📊 쿼터 사용: {self.quota.summary()}")
        return records