"""
공식 API 통합 Enrichment 모듈 (v5.0 - L2 지능형 라우팅 통합)
- 메뉴: NAVER API HUB 3단계 (블로그 → 카페글 → 뉴스)
- 유령가게: NAVER API HUB 3중 검증
- 좌표: 네이버 지오코딩(NCP) + 카카오 로컬 상호 검증
- ✅ 7계층 방어: 관련성 → 스팸 → 중복 → NER → 사전 대조 → 메뉴-가게 일치 → LLM
- ✅ L2 지능형 라우팅: LLM 호출 70% 감소
- ✅ Dual-SSOT: config.nlp_constants + config.taxonomy 참조
"""

import os
import json
import asyncio
import re
import hashlib
import httpx
from datetime import date, datetime
from typing import List, Dict, Optional, Tuple, Set
from math import radians, cos, sin, asin, sqrt

try:
    from ..ner.core.ner_pipeline import NERMenuPipeline
    from ..ner.core.llm_verifier import LLMVerifier
    from ..config.nlp_constants import (
        MENU_VOCABULARY, MENU_PATTERN_BLACKLIST, POSTFIX_PATTERNS,
        MENU_EXTRACTION_PATTERNS, SPAM_TEXT_SIGNATURES,
        CAFE_BIZ_KEYWORDS, MEAL_MENU_KEYWORDS,
    )
except ImportError:
    from data_pipeline.ner.core.ner_pipeline import NERMenuPipeline
    from data_pipeline.ner.core.llm_verifier import LLMVerifier
    from data_pipeline.config.nlp_constants import (
        MENU_VOCABULARY, MENU_PATTERN_BLACKLIST, POSTFIX_PATTERNS,
        MENU_EXTRACTION_PATTERNS, SPAM_TEXT_SIGNATURES,
        CAFE_BIZ_KEYWORDS, MEAL_MENU_KEYWORDS,
    )

try:
    from ..adapters.base import CertRecord
except ImportError:
    from data_pipeline.adapters.base import CertRecord

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE_DIR, '..', 'out')

QUOTA_PATH = os.path.join(OUT_DIR, 'official_enrich_quota.json')
CHECKPOINT_PATH = os.path.join(OUT_DIR, 'official_enrich_progress.json')
ENRICH_CACHE_PATH = os.path.join(OUT_DIR, 'official_enrich_cache.json')

DAILY_LIMITS = {
    'kakao': 20_000, 'naver_blog': 8_000, 'naver_cafe': 5_000,
    'naver_news': 3_000, 'naver_geo': 2_000,
}

NAVER_HUB_BASE = "https://naverapihub.apigw.ntruss.com"
NAVER_BLOG_URL = f"{NAVER_HUB_BASE}/search/v1/blog"
NAVER_NEWS_URL = f"{NAVER_HUB_BASE}/search/v1/news"
NAVER_CAFE_URL = f"{NAVER_HUB_BASE}/search/v1/cafearticle"
NAVER_GEO_URL = "https://maps.apigw.ntruss.com/map-geocode/v2/geocode"
KAKAO_LOCAL_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"


class QuotaExceededError(Exception):
    pass


class QuotaTracker:
    def __init__(self):
        self.state = self._load()

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


def _clean_menu_text(text: str) -> str:
    text = text.strip()
    for pattern in POSTFIX_PATTERNS:
        text = re.sub(pattern, '', text)
    return text.strip()


def _is_valid_menu(menu: str, store_name: str) -> bool:
    if not menu or len(menu) < 2 or len(menu) > 15:
        return False
    if menu in MENU_PATTERN_BLACKLIST:
        return False
    if store_name and menu in store_name:
        return False
    if re.match(r'^\d+$', menu):
        return False
    if re.search(r'[^\uAC00-\uD7A3a-zA-Z0-9\s]', menu):
        return False
    return True


def extract_menu_with_validation(text: str, store_name: str) -> Tuple[Optional[str], List[str], Optional[str]]:
    candidates = []
    for pattern, pattern_name in MENU_EXTRACTION_PATTERNS:
        matches = re.findall(pattern, text)
        for match in matches:
            cleaned = _clean_menu_text(match)
            if _is_valid_menu(cleaned, store_name):
                candidates.append((cleaned, pattern_name))

    if not candidates:
        return None, [], None

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
            'menu_blog': 0, 'menu_cafe': 0, 'menu_news': 0, 'menu_none': 0,
            'menu_blog_ner': 0, 'menu_cafe_ner': 0, 'menu_news_ner': 0,
            'ghost_open': 0, 'ghost_closed': 0, 'ghost_unknown': 0,
            'coord_high': 0, 'coord_medium': 0, 'coord_low': 0, 'skipped': 0,
            # 🆕 L2 라우팅 통계
            'route_ner_direct': 0, 'route_discard': 0, 'route_llm': 0,
        }

        self._seen_fingerprints: Set[str] = set()
        self._food_names_set = self._load_menu_vocabulary()
        self._relevance_hits = 0
        self._spam_hits = 0
        self._duplicate_hits = 0
        self._dictionary_hits = 0
        self._biz_mismatch_hits = 0
        self._proximity_hits = 0

        self.ner_pipeline: Optional[NERMenuPipeline] = None
        try:
            self.ner_pipeline = NERMenuPipeline()
            print(f"   NER 파이프라인: ✅")
        except Exception as e:
            print(f"   NER 파이프라인: ❌ 초기화 실패 ({e})")

        self.llm_verifier: Optional[LLMVerifier] = None
        self._llm_verify_count = 0
        self._llm_fill_count = 0
        self._llm_success_count = 0
        try:
            self.llm_verifier = LLMVerifier()
            if self.llm_verifier and self.llm_verifier.available:
                print(f"   LLM 검증기: ✅ (Layer 7 활성화)")
            else:
                print(f"   LLM 검증기: ⚠️ Ollama 미실행 (Layer 7 비활성화)")
                self.llm_verifier = None
        except Exception as e:
            print(f"   LLM 검증기: ❌ 초기화 실패 ({e})")
            self.llm_verifier = None

    def _load_menu_vocabulary(self) -> set:
        print(f"   📚 메뉴 어휘 사전 로드: {len(MENU_VOCABULARY)}개 (SSOT)")
        return MENU_VOCABULARY

    def _is_relevant_text(self, text: str, store_name: str) -> bool:
        if not text or not store_name:
            return False
        store_norm = re.sub(r'[^가-힣a-zA-Z0-9]', '', store_name).lower()
        text_norm = re.sub(r'[^가-힣a-zA-Z0-9]', '', text).lower()
        if len(store_norm) < 3:
            return False
        if store_norm in text_norm:
            return True
        if len(store_norm) >= 5:
            prefix = store_norm[:4]
            suffix = store_norm[-4:]
            skip_prefixes = ('제주', '서울', '부산', '인천', '대구', '대전', '광주', '울산', '경기', '강원', '충북', '충남', '전북', '전남', '경북', '경남')
            if prefix not in skip_prefixes:
                if prefix in text_norm or suffix in text_norm:
                    return True
        self._relevance_hits += 1
        return False

    def _is_spam_text(self, text: str) -> bool:
        for pattern in SPAM_TEXT_SIGNATURES:
            if pattern in text:
                self._spam_hits += 1
                return True
        return False

    def _compute_fingerprint(self, text: str) -> str:
        normalized = re.sub(r'\s+', '', text)[:300]
        return hashlib.md5(normalized.encode('utf-8', errors='ignore')).hexdigest()

    def _is_duplicate_fingerprint(self, text: str) -> bool:
        fingerprint = self._compute_fingerprint(text)
        if fingerprint in self._seen_fingerprints:
            self._duplicate_hits += 1
            return True
        self._seen_fingerprints.add(fingerprint)
        return False

    def _validate_menu_against_dictionary(self, menu: str) -> bool:
        if not menu or len(menu) < 2:
            return False
        if not self._food_names_set:
            return True
        if menu in self._food_names_set:
            return True
        for food_name in self._food_names_set:
            if len(food_name) >= 3 and food_name in menu:
                return True
        self._dictionary_hits += 1
        return False

    def _filter_valid_menus(self, menus: List[str]) -> List[str]:
        if not self._food_names_set:
            return menus
        return [m for m in menus if self._validate_menu_against_dictionary(m)]

    def _validate_menu_for_business(self, menu: str, record: CertRecord) -> bool:
        biz_type = (record.biz_type or '').lower()
        menu_lower = menu.lower()
        is_cafe_biz = any(kw in biz_type for kw in CAFE_BIZ_KEYWORDS)
        is_meal_menu = any(kw in menu_lower for kw in MEAL_MENU_KEYWORDS)
        if is_cafe_biz and is_meal_menu:
            self._biz_mismatch_hits += 1
            return False
        return True

    def _is_menu_near_store(self, text: str, store_name: str, menu: str, window: int = 80) -> bool:
        if not menu or not store_name:
            return False
        menu_norm = re.sub(r'\s+', '', menu)
        store_norm = re.sub(r'\s+', '', store_name)
        text_norm = re.sub(r'\s+', '', text)

        store_positions = []
        start = 0
        while True:
            pos = text_norm.find(store_norm, start)
            if pos == -1:
                break
            store_positions.append(pos)
            start = pos + 1

        if not store_positions:
            return False

        menu_positions = []
        start = 0
        while True:
            pos = text_norm.find(menu_norm, start)
            if pos == -1:
                break
            menu_positions.append(pos)
            start = pos + 1

        if not menu_positions:
            return False

        for sp in store_positions:
            for mp in menu_positions:
                if abs(sp - mp) <= window:
                    return True

        self._proximity_hits += 1
        return False

    def _apply_layer6_validation(self, menus: List[str], text: str,
                                  record: CertRecord) -> List[str]:
        consistent_menus = []
        for menu in menus:
            if not self._validate_menu_for_business(menu, record):
                continue
            if not self._is_menu_near_store(text, record.raw_name, menu):
                continue
            consistent_menus.append(menu)
        return consistent_menus

    # ─────────────── 🆕 L2: 지능형 라우팅 (클래스 메서드) ───────────────
    def _smart_route(self, text: str, store_name: str, ner_menus: List[str]) -> str:
        """
        LLM 호출 여부 지능 판단 (L2)
        - ner_direct: NER가 2개 이상 + 사전 검증 통과 → LLM 스킵
        - discard: 스팸/무관 텍스트 → 즉시 폐기
        - llm: 애매한 케이스만 LLM 호출
        """
        # Route B: 스팸/무관 텍스트 즉시 폐기
        if self._is_spam_text(text) or not self._is_relevant_text(text, store_name):
            self.stats['route_discard'] += 1
            return 'discard'
        
        # Route A: NER 확신 높음 → LLM 스킵
        if len(ner_menus) >= 2 and all(
            self._validate_menu_against_dictionary(m) for m in ner_menus
        ):
            self.stats['route_ner_direct'] += 1
            return 'ner_direct'
        
        # Route C: 애매한 케이스만 LLM
        self.stats['route_llm'] += 1
        return 'llm'

    async def _apply_llm_verification(
        self,
        client: httpx.AsyncClient,
        text: str,
        record: CertRecord,
        ner_menus: List[str]
    ) -> Tuple[Optional[str], List[str], Optional[str]]:
        """Layer 7: LLM 삼각 검증"""
        if not self.llm_verifier or not self.llm_verifier.available:
            return None, [], None

        biz_type = getattr(record, 'biz_type', '') or ''

        try:
            if ner_menus:
                self._llm_verify_count += 1
                verified = await self.llm_verifier.verify_menu_with_context(
                    text=text,
                    store_name=record.raw_name,
                    ner_menus=ner_menus,
                    biz_type=biz_type,
                )
                if verified:
                    self._llm_success_count += 1
                    return verified[0], verified[1:6], 'llm_verified'
            else:
                self._llm_fill_count += 1
                extracted = await self.llm_verifier.extract_menu_direct(
                    text=text,
                    store_name=record.raw_name,
                    biz_type=biz_type,
                )
                if extracted:
                    final_menus = self._apply_layer6_validation(
                        extracted, text, record
                    )
                    if final_menus:
                        self._llm_success_count += 1
                        return final_menus[0], final_menus[1:6], 'llm_filled'
        except Exception as e:
            print(f"   ⚠️ LLM 검증 실패: {e}")

        return None, [], None

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

    async def _search_naver_hub(self, client: httpx.AsyncClient,
                                 api_type: str, query: str,
                                 display: int = 5) -> List[dict]:
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

    # ─────────────── 🔧 결함 수정: process_items 실제 호출 ───────────────
    async def _enrich_menu_full(self, client: httpx.AsyncClient,
                                 record: CertRecord) -> Tuple[Optional[str], List[str], Optional[str]]:
        """3단계 메뉴 enrichment (L2 라우팅 적용)"""

        search_queries = [
            f'{record.raw_name} 메뉴',
            f'{record.raw_name} 메뉴판',
            f'{record.raw_name} 대표',
            f'{record.raw_name} 추천',
            f'{record.raw_name} 시그니처',
            f'{record.raw_name} 맛집',
        ]
        
        async def process_items(items, source_prefix):
            """개별 item을 처리하는 내부 함수"""
            for item in items:
                text = _clean_html((item.get('title', '') or '') + ' ' + (item.get('description', '') or ''))
                
                # Layer 2-1, 2-2, 2-3 검증
                if not self._is_relevant_text(text, record.raw_name):
                    continue
                if self._is_spam_text(text):
                    continue
                if self._is_duplicate_fingerprint(text):
                    continue

                # 1단계: NER로 메뉴 추출 시도
                all_ner_menus = []
                if self.ner_pipeline:
                    ner_res = self.ner_pipeline.extract_menus_from_text(
                        text=text, store_name=record.raw_name, source=source_prefix
                    )
                    all_ner_menus = ner_res.get('all_menus', [])
                    validated = self._apply_layer6_validation(
                        self._filter_valid_menus(all_ner_menus), text, record
                    )
                    if validated:
                        return validated[0], validated[1:6], f'naver_{source_prefix}_ner'

                # 2단계: 패턴 매칭
                menu, extras, _ = extract_menu_with_validation(text, record.raw_name)
                if menu and self._validate_menu_against_dictionary(menu) and \
                   self._validate_menu_for_business(menu, record) and \
                   self._is_menu_near_store(text, record.raw_name, menu):
                    return menu, extras, f'naver_{source_prefix}'

                # 3단계: 🆕 L2 지능형 라우팅 적용
                route = self._smart_route(text, record.raw_name, all_ner_menus)
                
                if route == 'ner_direct':
                    # NER 확신 높음 → LLM 스킵, NER 결과 사용
                    if all_ner_menus:
                        return all_ner_menus[0], all_ner_menus[1:6], f'naver_{source_prefix}_ner_high'
                
                elif route == 'discard':
                    # 스팸/무관 → 다음 item으로
                    continue
                
                elif route == 'llm' and all_ner_menus:
                    # 애매 + NER 후보 있음 → LLM 검증
                    llm_menu, llm_extras, llm_source = await self._apply_llm_verification(
                        client, text, record, all_ner_menus
                    )
                    if llm_menu:
                        return llm_menu, llm_extras, f"naver_{source_prefix}_{llm_source}"
            
            return None, [], None

        # 🆕 실제로 각 소스별 items를 process_items에 전달
        for q in search_queries:
            blog_items = await self._search_naver_hub(client, 'blog', q, 10)
            res = await process_items(blog_items, 'blog')
            if res[0]:
                return res

        for q in search_queries[:2]:
            cafe_items = await self._search_naver_hub(client, 'cafe', q, 5)
            res = await process_items(cafe_items, 'cafe')
            if res[0]:
                return res

        news_items = await self._search_naver_hub(client, 'news', search_queries[0], 5)
        res = await process_items(news_items, 'news')
        if res[0]:
            return res

        return None, [], None

    async def _verify_ghost_full(self, client: httpx.AsyncClient,
                                  record: CertRecord) -> int:
        score = 0
        now = datetime.now()

        news_items = await self._search_naver_hub(client, 'news', f'{record.raw_name} 폐업', display=3)
        for item in news_items:
            text = _clean_html((item.get('title', '') or '') + ' ' + (item.get('description', '') or '')).lower()
            if any(kw in text for kw in ['폐업', '문을 닫', '없어졌', '사라졌']):
                score += 50
                break

        blog_items = await self._search_naver_hub(client, 'blog', f'{record.raw_name} 폐업', display=3)
        for item in blog_items:
            text = _clean_html((item.get('title', '') or '') + ' ' + (item.get('description', '') or '')).lower()
            if any(kw in text for kw in ['폐업', '문을 닫', '없어졌', '사라졌', '닫았']):
                score += 30
                break

        cafe_items = await self._search_naver_hub(client, 'cafe', f'{record.raw_name} 폐업', display=3)
        for item in cafe_items:
            text = _clean_html((item.get('title', '') or '') + ' ' + (item.get('description', '') or '')).lower()
            if any(kw in text for kw in ['폐업', '문을 닫', '없어졌']):
                score += 20
                break

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
                    if not record.main_menu:
                        cache_key = f"menu|{record.dedup_key}"
                        cached = self.ecache.get(cache_key)

                        if cached is not None:
                            if cached.get('menu'):
                                record.main_menu = cached['menu']
                                source = cached.get('source')

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
                    ner_total = self.stats.get('menu_blog_ner', 0) + \
                                self.stats.get('menu_cafe_ner', 0) + \
                                self.stats.get('menu_news_ner', 0)
                    pattern_total = self.stats['menu_blog'] + \
                                    self.stats['menu_cafe'] + \
                                    self.stats['menu_news']
                    print(f"   진행: {idx}/{total} "
                          f"(패턴:{pattern_total} NER:{ner_total}, "
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

        print(f"\n🛡️ 다층 방어 통계:")
        print(f"   Layer 2-1 관련성 미달 차단: {self._relevance_hits}건")
        print(f"   Layer 2-2 스팸 패턴 차단: {self._spam_hits}건")
        print(f"   Layer 2-3 중복 본문 차단: {self._duplicate_hits}건 (고유 본문: {len(self._seen_fingerprints)}개)")
        print(f"   Layer 4 사전 미매칭 차단: {self._dictionary_hits}건")
        print(f"   Layer 6-1 업종-메뉴 불일치 차단: {self._biz_mismatch_hits}건")
        print(f"   Layer 6-2 근접성 검증 차단: {self._proximity_hits}건")

        # 🆕 L2 라우팅 통계
        print(f"\n🧭 L2 지능형 라우팅 통계:")
        print(f"   Route A (NER 직접): {self.stats['route_ner_direct']}건")
        print(f"   Route B (즉시 폐기): {self.stats['route_discard']}건")
        print(f"   Route C (LLM 호출): {self.stats['route_llm']}건")

        print(f"\n🤖 LLM 삼각 검증 통계 (Layer 7):")
        print(f"   검증 모드 (NER결과 교차검증): {self._llm_verify_count}회")
        print(f"   보충 모드 (NER실패 직접추출): {self._llm_fill_count}회")
        print(f"   LLM 최종 확정 성공: {self._llm_success_count}회")

        print(f"📊 쿼터 사용: {self.quota.summary()}")
        return records