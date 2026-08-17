# data_pipeline/enrich/ner/preprocessing/food_db_integrator.py
"""
음식 DB 통합 전처리 + 메뉴 정규화 사전 생성 (Phase 1) - v1.1
=================================================
수정 사항:
- ✅ 음식DB 한글 컬럼명('식품명') 지원
- ✅ 메뉴 정규화 base_menu 로직 개선 (재료 매칭 실패 시 fallback)
- ✅ 음식DB 카테고리 추출 개선 (식품대분류명 컬럼 활용)
"""
import os
import csv
import re
import glob
import json
import random
from typing import Set, Dict, Optional, List, Tuple

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
os.makedirs(DATA_DIR, exist_ok=True)


# ═══════════════════════════════════════════════════════════
# Phase 1: 음식DB 카테고리 필터링 설정
# ═══════════════════════════════════════════════════════════
EXCLUDE_CATEGORIES = {
    '피자', '햄버거', '버거', '핫도그',
    '스무디', '라떼', '에이드', '아이스티', '커피', '액상커피',
    '기타차', '허브차', '율무차', '오미자차', '귤차', '콤부차',
    '밀크티', '버블티', '탄산음료', '과채주스', '우유', '요구르트',
    '케이크', '머핀', '파이', '만주', '토스트',
    '샌드위치', '스콘', '마들렌', '쿠키', '도넛', '빙수',
    '아이스크림', '젤라또', '쉐이크', '프라페', '블렌디드',
    '브라우니', '마카롱', '티라미수', '파르페', '선데',
    '소르베', '셔벗', '프로즌', '요거트', '푸딩', '무스',
    '타르트', '에클레어', '크루아상', '크로와상', '데니쉬', '브레드',
    '바게트', '치아바타', '포카치아', '베이글', '크로플',
    '와플', '파운드케이크', '비스킷', '크래커',
    '크로켓', '고로케', '롤빵', '마늘빵', '감자빵', '건포도빵',
    '계란빵', '기타빵', '츄러스', '페이스트리',
    '팝콘', '견과', '과일', '소시지', '시리얼', '그래놀라',
    '오트밀', '뮤즐리', '잼', '꿀', '시럽', '소스', '드레싱',
    '마요네즈', '케첩', '머스타드', '피클',
    '달걀', '고구마',
}


# ═══════════════════════════════════════════════════════════
# Phase 2: 가공식품DB 조리 패턴
# ═══════════════════════════════════════════════════════════
PROCESSED_COOKING_PATTERNS = [
    '찌개', '전골', '국', '탕', '국밥', '해장국',
    '구이', '조림', '볶음', '찜', '무침', '나물',
    '전', '부침', '튀김', '강정', '탕수',
    '밥', '덮밥', '비빔밥', '볶음밥', '김밥', '죽',
    '국수', '냉면', '막국수', '라면', '우동', '소바',
    '짜장', '짬뽕', '떡볶이', '쫄면', '만두', '순대',
    '회', '초밥', '물회', '회무침',
    '산적', '잡채', '냉채', '생채', '겉절이', '김치',
    '장아찌', '젓갈', '포', '편', '적', '말이', '꼬치',
    '스프', '수프', '그라탕', '라자냐', '뇨끼', '리소토',
    '파스타', '스파게티', '카레', '커리',
    '스테이크', '함박', '돈까스', '돈카츠', '까스', '커틀릿',
    '샐러드', '포케', '랩', '부리또', '타코', '퀘사디아',
    '샌드위치', '토스트', '파니니', '산도',
    '오므라이스', '하이라이스', '규동', '가츠동',
]

PROCESSED_EXCLUDE_KEYWORDS = [
    '세트', '메뉴', '요리', '음식', '식사', '간식',
    '레디팩', '스틱바', '컵빙수',
]


# ═══════════════════════════════════════════════════════════
# Phase 4: 메뉴 정규화 사전 (신규)
# ═══════════════════════════════════════════════════════════

# ─── 조리법 접미사 (긴 것부터 매칭) ───
COOKING_SUFFIXES = [
    '양념구이', '양념찜', '양념볶음', '양념조림',
    '김치볶음밥', '김치찜', '김치전', '김치국',
    '볶음밥', '덮밥', '비빔밥', '잡채밥', '카레밥',
    '떡볶이', '라볶이', '떡만두국', '만두국',
    '순두부찌개', '된장찌개', '김치찌개', '부대찌개',
    '미역국', '콩나물국', '달걀국', '무국',
    '해장국', '매운탕', '고니탕', '알탕',
    '물냉면', '비빔냉면', '막국수', '칼국수',
    '잔치국수', '비빔국수', '열무국수',
    '짜장면', '짬뽕', '우동', '소바', '쌀국수',
    '스파게티', '파스타', '리소토', '리조또',
    '그라탕', '라자냐', '뇨끼', '파르팔레',
    '샌드위치', '토스트', '베이글', '크로플', '와플',
    '스테이크', '돈까스', '돈카츠', '커틀릿',
    '탕수', '강정', '튀김', '부침', '빈대떡',
    '산적', '화양적', '완자전', '해물파전',
    '김밥', '주먹밥', '유부초밥', '초밥',
    '샐러드', '스프', '수프', '죽',
    '구이', '찜', '볶음', '조림', '무침', '나물',
    '전', '적', '포', '편', '말이', '꼬치',
    '탕', '국', '찌개', '밥', '면', '국수',
    '회', '초밥', '롤', '랩', '쌈',
    '생채', '겉절이', '장아찌', '절임', '젓',
]

# ─── 재료 명사 사전 (확장) ───
INGREDIENT_NOUNS = [
    # 육류
    'LA갈비', 'LA돈갈비', 'LA꽃갈비', 'LA소갈비',
    '돼지고기', '소고기', '닭고기', '오리고기', '양고기',
    '목살', '삼겹살', '앞다리', '갈비', '불고기', '제육',
    '차돌', '대패삼겹살', '대패목살', '대패닭가슴살',
    '대패소불고기', '한우', '흑돼지', '돈육', '돈안심',
    '등심', '안심', '채끝', '부채살', '치마살', '살치살',
    '사태', '양지', '우둔', '홍두깨', '업진', '도가니',
    '곱창', '대창', '막창', '염통', '간', '양', '천엽',
    '족발', '보쌈', '수육', '편육',
    # 해산물
    '가리비', '가리비관자', '가리비살', '오징어', '문어',
    '낙지', '주꾸미', '새우', '알새우', '감바스',
    '꽃게', '대게', '게', '조개', '바지락', '홍합',
    '전복', '연어', '참치', '고등어', '갈치', '삼치',
    '가자미', '동태', '명태', '코다리', '굴비', '조기',
    '임연수', '달고기', '장어', '꼼장어', '복어', '병어',
    '도미', '도루묵', '양미리', '쥐포', '꼴뚜기', '명란',
    '날치알', '한치', '알짬뽕', '우럭', '광어', '넙치',
    '민어', '참돔', '방어', '부시리', '옥돔', '몸통',
    # 채소
    '애호박', '단호박', '고구마', '감자', '당근', '양파',
    '파', '부추', '미나리', '시금치', '청경채', '배추',
    '알배추', '열무', '무', '오이', '가지', '꽈리고추',
    '고추', '풋고추', '피망', '브로콜리', '양배추',
    '아스파라거스', '연근', '우엉', '죽순', '콩나물',
    '숙주나물', '고구마줄기', '머위', '망초', '고들빼기',
    '궁채', '쑥', '달래', '냉이', '두릅', '취나물',
    '시래기', '우거지', '곤드레', '고사리', '도라지',
    '버섯', '느타리버섯', '새송이버섯', '양송이버섯', '표고버섯',
    '팽이버섯', '목이버섯',
    # 기타
    '두부', '어묵', '달걀', '계란', '메추리알', '만두',
    '떡', '가래떡', '밀떡', '쫄면', '견과류', '크랜베리',
    '아몬드', '호두', '동부묵', '묵',
    # 가공식품DB에서 자주 나오는 재료
    '닭가슴살', '닭다리', '닭봉', '닭안심', '닭목살',
    '누룽지', '가마솥', '삼계', '백숙',
    '마늘쫑', '마늘종', '진미채', '미역', '다시마',
    '톳', '파래', '해초', '해산물', '해물',
]

# ─── 수식어 사전 ───
MODIFIER_PREFIXES = [
    '양념', '매콤', '달콤', '매운', '짭짤', '고소',
    '간장', '고추장', '된장', '마요', '갈릭', '크림',
    '치즈', '버터', '들기름', '참기름', '매실',
    '카레', '허브', '로제', '토마토', '바질', '페스토',
    '레몬', '유자', '한라봉', '청귤', '흑임자',
    '경양식', '동남아식', '동남아풍', '동경식', '동북',
    '일식', '중식', '양식', '한식',
    '숯불', '직화', '연탄', '오븐', '수비드',
    '매콤', '스파이시', '데리야끼', '칠리',
]


def decompose_menu_name(name: str) -> Dict:
    """메뉴명을 3층 구조로 분해"""
    result = {
        'original': name,
        'ingredients': [],
        'modifiers': [],
        'cooking': None,
        'base_menu': None,
    }
    
    remaining = name
    
    # 1. 조리법 접미사 추출 (긴 것부터)
    for suffix in COOKING_SUFFIXES:
        if remaining.endswith(suffix):
            result['cooking'] = suffix
            remaining = remaining[:-len(suffix)]
            break
    
    # 2. 수식어 접두사 추출 (반복)
    changed = True
    while changed:
        changed = False
        for prefix in MODIFIER_PREFIXES:
            if remaining.startswith(prefix):
                result['modifiers'].append(prefix)
                remaining = remaining[len(prefix):]
                changed = True
                break
    
    # 3. 재료 명사 추출 (남은 텍스트에서)
    for noun in INGREDIENT_NOUNS:
        if noun in remaining:
            result['ingredients'].append(noun)
    
    # 4. 기본 메뉴명 구성 (개선된 로직)
    base_parts = []
    if result['ingredients']:
        base_parts.extend(result['ingredients'])
    if result['cooking']:
        base_parts.append(result['cooking'])
    
    if base_parts:
        result['base_menu'] = ''.join(base_parts)
    elif result['cooking']:
        # 재료가 매칭되지 않았지만 조리법이 있으면 원본에서 조리법만 제거
        result['base_menu'] = name[:-len(result['cooking'])] if name.endswith(result['cooking']) else name
    else:
        result['base_menu'] = name
    
    return result


def build_normalization_dictionary(food_names: Set[str]) -> Dict:
    """메뉴 정규화 사전 생성"""
    base_menus = {}
    
    for name in food_names:
        decomposed = decompose_menu_name(name)
        base = decomposed['base_menu']
        
        if not base or len(base) < 2:
            continue
        
        if base not in base_menus:
            base_menus[base] = {
                'variants': [],
                'cooking_methods': set(),
                'modifiers': set(),
                'ingredients': set(),
            }
        
        base_menus[base]['variants'].append(name)
        if decomposed['cooking']:
            base_menus[base]['cooking_methods'].add(decomposed['cooking'])
        base_menus[base]['modifiers'].update(decomposed['modifiers'])
        base_menus[base]['ingredients'].update(decomposed['ingredients'])
    
    # Set을 List로 변환
    for base in base_menus:
        base_menus[base]['variants'].sort(key=len, reverse=True)
        base_menus[base]['cooking_methods'] = sorted(base_menus[base]['cooking_methods'])
        base_menus[base]['modifiers'] = sorted(base_menus[base]['modifiers'])
        base_menus[base]['ingredients'] = sorted(base_menus[base]['ingredients'])
    
    stats = {
        'total_base_menus': len(base_menus),
        'total_variants': sum(len(b['variants']) for b in base_menus.values()),
        'avg_variants_per_base': sum(len(b['variants']) for b in base_menus.values()) / len(base_menus) if base_menus else 0,
        'max_variants': max(len(b['variants']) for b in base_menus.values()) if base_menus else 0,
    }
    
    return {
        'version': '1.1',
        'base_menus': base_menus,
        'stats': stats,
        'cooking_suffixes': COOKING_SUFFIXES,
        'ingredient_nouns': INGREDIENT_NOUNS,
        'modifier_prefixes': MODIFIER_PREFIXES,
    }


# ═══════════════════════════════════════════════════════════
# 메인 클래스
# ═══════════════════════════════════════════════════════════
class FoodDBIntegrator:
    """음식 DB 통합 전처리 + 메뉴 정규화"""

    def __init__(self):
        self.food_names: Set[str] = set()
        self.stats = {
            'food_db_total': 0,
            'food_db_excluded_category': 0,
            'food_db_excluded_invalid': 0,
            'food_db_included': 0,
            'processed_db_total': 0,
            'processed_db_excluded_keyword': 0,
            'processed_db_excluded_pattern': 0,
            'processed_db_included': 0,
        }
        self.excluded_category_counts: Dict[str, int] = {}

    def _find_file(self, pattern: str) -> Optional[str]:
        search_patterns = [
            os.path.join(DATA_DIR, pattern),
            os.path.join(DATA_DIR, '**', pattern),
            os.path.join(BASE_DIR, '..', '..', 'data', pattern),
            os.path.join(BASE_DIR, '..', '..', '..', pattern),
        ]
        for sp in search_patterns:
            matches = glob.glob(sp, recursive=True)
            if matches:
                matches.sort(key=os.path.getsize, reverse=True)
                return matches[0]
        return None

    def _clean_food_name(self, name: str) -> str:
        if not name:
            return ''
        name = re.sub(r'\s*\([LRMSXFPG]+\)', '', name)
        name = re.sub(r'\s*\(Tall\)|\(Grande\)|\(Venti\)|\(Mini Venti\)', '', name)
        name = re.sub(r'\s*\(초대용량\)|\(빅사이즈\)|\(1인\)|\(2인\)', '', name)
        name = re.sub(r'\s*\(Solo\)|\(Double\)|\(Triple\)', '', name)
        name = re.sub(r'\s*아이스\s*\(ICED\)|\s*핫\s*\(HOT\)', '', name)
        name = re.sub(r'\s*ICED|\s*HOT', '', name)
        name = re.sub(r'[^\uAC00-\uD7A3a-zA-Z0-9\s]', '', name)
        name = re.sub(r'\s+', ' ', name).strip()
        return name

    def _extract_category(self, raw_name: str) -> Optional[str]:
        if '_' in raw_name:
            return raw_name.split('_', 1)[0].strip()
        return None

    def _extract_food_name(self, raw_name: str) -> str:
        if '_' in raw_name:
            name = raw_name.split('_', 1)[1]
        else:
            name = raw_name
        return self._clean_food_name(name)

    def _normalize_category(self, category: str) -> str:
        if not category:
            return ''
        category = re.sub(r'[()/\s]', '', category)
        return category.lower()

    def _is_excluded_category(self, category: Optional[str]) -> bool:
        if not category:
            return False
        normalized = self._normalize_category(category)
        for excluded in EXCLUDE_CATEGORIES:
            excluded_norm = excluded.lower()
            if normalized == excluded_norm:
                return True
            if excluded_norm in normalized and len(excluded_norm) >= 3:
                return True
        return False

    def load_food_db(self, path: Optional[str]) -> None:
        """음식DB 로드 (한글 컬럼명 '식품명' 지원)"""
        if not path or not os.path.exists(path):
            print(f"⚠️ 음식DB 없음: {path}")
            return

        print(f"📄 음식DB 로드: {os.path.basename(path)}")
        size_mb = os.path.getsize(path) / (1024 * 1024)
        print(f"   크기: {size_mb:.2f} MB")

        with open(path, 'r', encoding='utf-8-sig', newline='') as f:
            reader = csv.DictReader(f)
            
            # ✅ 헤더 확인 및 디버깅
            if reader.fieldnames:
                print(f"   헤더 컬럼 수: {len(reader.fieldnames)}개")
                # 실제 컬럼명 확인
                food_name_cols = [c for c in reader.fieldnames if '식품' in c or 'food' in c.lower()]
                if food_name_cols:
                    print(f"   음식명 관련 컬럼: {food_name_cols[:5]}")
            
            for row in reader:
                # ✅ 한글 컬럼명 우선, 영문 컬럼명으로 fallback
                raw_name = (
                    row.get('식품명', '') or 
                    row.get('food_name', '') or
                    row.get('대표식품명', '')
                )
                if not raw_name:
                    continue

                self.stats['food_db_total'] += 1
                category = self._extract_category(raw_name)

                if self._is_excluded_category(category):
                    self.stats['food_db_excluded_category'] += 1
                    cat_key = self._normalize_category(category)
                    self.excluded_category_counts[cat_key] = \
                        self.excluded_category_counts.get(cat_key, 0) + 1
                    continue

                food_name = self._extract_food_name(raw_name)
                if food_name and len(food_name) >= 2:
                    self.food_names.add(food_name)
                    self.stats['food_db_included'] += 1
                else:
                    self.stats['food_db_excluded_invalid'] += 1

        print(f"✅ 음식DB 처리 완료:")
        print(f"   총 {self.stats['food_db_total']:,}건")
        print(f"   카테고리 제외: {self.stats['food_db_excluded_category']:,}건")
        print(f"   유효하지 않음: {self.stats['food_db_excluded_invalid']:,}건")
        print(f"   포함: {self.stats['food_db_included']:,}건")

    def load_processed_db(self, path: Optional[str]) -> None:
        """가공식품DB 로드"""
        if not path or not os.path.exists(path):
            print(f"⚠️ 가공식품DB 없음: {path}")
            return

        print(f"📄 가공식품DB 로드: {os.path.basename(path)}")
        size_mb = os.path.getsize(path) / (1024 * 1024)
        print(f"   크기: {size_mb:.2f} MB")

        with open(path, 'r', encoding='utf-8-sig', newline='') as f:
            reader = csv.DictReader(f)
            for row in reader:
                raw_name = (
                    row.get('식품명', '') or 
                    row.get('food_name', '')
                )
                if not raw_name:
                    continue

                self.stats['processed_db_total'] += 1

                if any(kw in raw_name for kw in PROCESSED_EXCLUDE_KEYWORDS):
                    self.stats['processed_db_excluded_keyword'] += 1
                    continue

                food_name = self._clean_food_name(raw_name)
                if not food_name or len(food_name) < 2:
                    self.stats['processed_db_excluded_keyword'] += 1
                    continue

                has_cooking_pattern = any(
                    p in food_name for p in PROCESSED_COOKING_PATTERNS
                )
                if has_cooking_pattern:
                    if food_name not in self.food_names:
                        self.food_names.add(food_name)
                        self.stats['processed_db_included'] += 1
                else:
                    self.stats['processed_db_excluded_pattern'] += 1

        print(f"✅ 가공식품DB 처리 완료:")
        print(f"   총 {self.stats['processed_db_total']:,}건")
        print(f"   키워드 제외: {self.stats['processed_db_excluded_keyword']:,}건")
        print(f"   패턴 미매칭: {self.stats['processed_db_excluded_pattern']:,}건")
        print(f"   포함: {self.stats['processed_db_included']:,}건")

    def save_results(self) -> None:
        sorted_names = sorted(self.food_names, key=len, reverse=True)

        names_path = os.path.join(DATA_DIR, 'food_names.txt')
        with open(names_path, 'w', encoding='utf-8') as f:
            for name in sorted_names:
                f.write(name + '\n')

        dict_path = os.path.join(DATA_DIR, 'food_dictionary.json')
        dictionary = {
            'version': '1.1',
            'total_unique_names': len(self.food_names),
            'stats': self.stats,
            'excluded_categories': dict(
                sorted(self.excluded_category_counts.items(), key=lambda x: -x[1])
            ),
            'names': sorted_names,
        }
        with open(dict_path, 'w', encoding='utf-8') as f:
            json.dump(dictionary, f, ensure_ascii=False, indent=2)

        print(f"\n💾 저장 완료:")
        print(f"   음식명 목록: {names_path}")
        print(f"   통계: {dict_path}")

    def build_and_save_normalization_dict(self) -> None:
        """메뉴 정규화 사전 생성 및 저장"""
        print("\n🔧 메뉴 정규화 사전 생성 중...")
        
        norm_dict = build_normalization_dictionary(self.food_names)
        
        norm_path = os.path.join(DATA_DIR, 'menu_normalization.json')
        with open(norm_path, 'w', encoding='utf-8') as f:
            json.dump(norm_dict, f, ensure_ascii=False, indent=2)
        
        print(f"✅ 메뉴 정규화 사전 생성 완료:")
        print(f"   기본 메뉴: {norm_dict['stats']['total_base_menus']:,}개")
        print(f"   총 변형: {norm_dict['stats']['total_variants']:,}개")
        print(f"   평균 변형 수: {norm_dict['stats']['avg_variants_per_base']:.1f}")
        print(f"   최대 변형 수: {norm_dict['stats']['max_variants']}")
        print(f"💾 저장: {norm_path}")
        
        # 샘플 출력
        print(f"\n📋 정규화 샘플 (10개):")
        sorted_bases = sorted(
            norm_dict['base_menus'].items(),
            key=lambda x: len(x[1]['variants']),
            reverse=True
        )
        for base, info in sorted_bases[:10]:
            print(f"   {base}")
            print(f"      변형: {info['variants'][:3]}...")
            print(f"      조리법: {info['cooking_methods']}")
            print(f"      수식어: {info['modifiers']}")
            print(f"      재료: {info['ingredients']}")

    def integrate(self) -> None:
        print("=" * 70)
        print("🍽️ 음식 DB 통합 전처리 (Phase 1) - v1.1")
        print("=" * 70)

        food_db_path = self._find_file('*음식DB*.csv')
        processed_db_path = self._find_file('*가공식품*.csv')

        if food_db_path:
            print(f"🔍 음식DB 발견: {os.path.basename(food_db_path)}")
        else:
            print("❌ 음식DB를 찾을 수 없습니다.")

        if processed_db_path:
            print(f"🔍 가공식품DB 발견: {os.path.basename(processed_db_path)}")
        else:
            print("❌ 가공식품DB를 찾을 수 없습니다.")

        print()

        self.load_food_db(food_db_path)
        print()
        self.load_processed_db(processed_db_path)

        self.save_results()
        self.build_and_save_normalization_dict()

        print(f"\n{'='*70}")
        print(f"📊 최종 통계")
        print(f"{'='*70}")
        print(f"총 고유 음식명: {len(self.food_names):,}개")

        print(f"\n📋 제외된 카테고리 TOP 10:")
        sorted_excluded = sorted(
            self.excluded_category_counts.items(),
            key=lambda x: -x[1]
        )[:10]
        for category, count in sorted_excluded:
            print(f"   ❌ {category}: {count:,}건")

        sample = random.sample(list(self.food_names), min(30, len(self.food_names)))
        print(f"\n📋 샘플 음식명 (30개):")
        for name in sample:
            print(f"   🍚 {name}")


if __name__ == '__main__':
    integrator = FoodDBIntegrator()
    integrator.integrate()