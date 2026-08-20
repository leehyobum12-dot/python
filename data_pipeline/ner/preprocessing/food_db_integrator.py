"""
음식 DB 통합 전처리 + 메뉴 정규화 사전 생성 (Phase 1) - v2.0 (SSOT 적용)
"""
import os, csv, re, glob, json, random
from typing import Set, Dict, Optional, List, Tuple

try:
    from ...config.constants import (
        EXCLUDE_CATEGORIES, COOKING_SUFFIXES, INGREDIENT_NOUNS, 
        MODIFIER_PREFIXES, PROCESSED_EXCLUDE_KEYWORDS
    )
except ImportError:
    from config.constants import (
        EXCLUDE_CATEGORIES, COOKING_SUFFIXES, INGREDIENT_NOUNS, 
        MODIFIER_PREFIXES, PROCESSED_EXCLUDE_KEYWORDS
    )

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
os.makedirs(DATA_DIR, exist_ok=True)

# 가공식품DB 전용 조리 패턴 (SSOT의 COOKING_SUFFIXES를 재활용하되, DB 특화 패턴이 있다면 추가)
PROCESSED_COOKING_PATTERNS = COOKING_SUFFIXES 

def decompose_menu_name(name: str) -> Dict:
    """메뉴명을 3층 구조로 분해 (SSOT 참조)"""
    result = {'original': name, 'ingredients': [], 'modifiers': [], 'cooking': None, 'base_menu': None}
    remaining = name
    
    for suffix in sorted(COOKING_SUFFIXES, key=len, reverse=True):
        if remaining.endswith(suffix):
            result['cooking'] = suffix
            remaining = remaining[:-len(suffix)]
            break
            
    changed = True
    while changed:
        changed = False
        for prefix in MODIFIER_PREFIXES:
            if remaining.startswith(prefix):
                result['modifiers'].append(prefix)
                remaining = remaining[len(prefix):]
                changed = True
                break
                
    for noun in INGREDIENT_NOUNS:
        if noun in remaining:
            result['ingredients'].append(noun)
            
    base_parts = result['ingredients'] + ([result['cooking']] if result['cooking'] else [])
    result['base_menu'] = ''.join(base_parts) if base_parts else name
    return result

def build_normalization_dictionary(food_names: Set[str]) -> Dict:
    """메뉴 정규화 사전 생성"""
    base_menus = {}
    for name in food_names:
        decomposed = decompose_menu_name(name)
        base = decomposed['base_menu']
        if not base or len(base) < 2: continue
        
        if base not in base_menus:
            base_menus[base] = {'variants': [], 'cooking_methods': set(), 'modifiers': set(), 'ingredients': set()}
            
        base_menus[base]['variants'].append(name)
        if decomposed['cooking']: base_menus[base]['cooking_methods'].add(decomposed['cooking'])
        base_menus[base]['modifiers'].update(decomposed['modifiers'])
        base_menus[base]['ingredients'].update(decomposed['ingredients'])
        
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
    return {'version': '2.0', 'base_menus': base_menus, 'stats': stats}

class FoodDBIntegrator:
    def __init__(self):
        self.food_names: Set[str] = set()
        self.stats = {'food_db_total': 0, 'food_db_excluded_category': 0, 'food_db_excluded_invalid': 0, 'food_db_included': 0,
                      'processed_db_total': 0, 'processed_db_excluded_keyword': 0, 'processed_db_excluded_pattern': 0, 'processed_db_included': 0}
        self.excluded_category_counts: Dict[str, int] = {}

    def _find_file(self, pattern: str) -> Optional[str]:
        search_patterns = [os.path.join(DATA_DIR, pattern), os.path.join(DATA_DIR, '**', pattern)]
        for sp in search_patterns:
            matches = glob.glob(sp, recursive=True)
            if matches: return sorted(matches, key=os.path.getsize, reverse=True)[0]
        return None

    def _clean_food_name(self, name: str) -> str:
        if not name: return ''
        name = re.sub(r'\s*\([LRMSXFPG]+\)|\s*\(Tall\)|\(Grande\)|\(Venti\)|\(Mini Venti\)', '', name)
        name = re.sub(r'\s*\(초대용량\)|\(빅사이즈\)|\(1인\)|\(2인\)|\s*\(Solo\)|\(Double\)|\(Triple\)', '', name)
        name = re.sub(r'\s*아이스\s*\(ICED\)|\s*핫\s*\(HOT\)|\s*ICED|\s*HOT', '', name)
        name = re.sub(r'[^\uAC00-\uD7A3a-zA-Z0-9\s]', '', name)
        return re.sub(r'\s+', ' ', name).strip()

    def _extract_category(self, raw_name: str) -> Optional[str]:
        return raw_name.split('_', 1)[0].strip() if '_' in raw_name else None

    def _extract_food_name(self, raw_name: str) -> str:
        name = raw_name.split('_', 1)[1] if '_' in raw_name else raw_name
        return self._clean_food_name(name)

    def _normalize_category(self, category: str) -> str:
        return re.sub(r'[()/\s]', '', category).lower() if category else ''

    def _is_excluded_category(self, category: Optional[str]) -> bool:
        if not category: return False
        normalized = self._normalize_category(category)
        for excluded in EXCLUDE_CATEGORIES:
            excluded_norm = excluded.lower()
            if normalized == excluded_norm or (excluded_norm in normalized and len(excluded_norm) >= 3):
                return True
        return False

    def load_food_db(self, path: Optional[str]) -> None:
        if not path or not os.path.exists(path): return
        print(f"📄 음식DB 로드: {os.path.basename(path)}")
        with open(path, 'r', encoding='utf-8-sig', newline='') as f:
            for row in csv.DictReader(f):
                raw_name = row.get('식품명', '') or row.get('food_name', '') or row.get('대표식품명', '')
                if not raw_name: continue
                self.stats['food_db_total'] += 1
                if self._is_excluded_category(self._extract_category(raw_name)):
                    self.stats['food_db_excluded_category'] += 1
                    continue
                food_name = self._extract_food_name(raw_name)
                if food_name and len(food_name) >= 2:
                    self.food_names.add(food_name)
                    self.stats['food_db_included'] += 1
                else:
                    self.stats['food_db_excluded_invalid'] += 1

    def load_processed_db(self, path: Optional[str]) -> None:
        if not path or not os.path.exists(path): return
        print(f"📄 가공식품DB 로드: {os.path.basename(path)}")
        with open(path, 'r', encoding='utf-8-sig', newline='') as f:
            for row in csv.DictReader(f):
                raw_name = row.get('식품명', '') or row.get('food_name', '')
                if not raw_name: continue
                self.stats['processed_db_total'] += 1
                if any(kw in raw_name for kw in PROCESSED_EXCLUDE_KEYWORDS):
                    self.stats['processed_db_excluded_keyword'] += 1
                    continue
                food_name = self._clean_food_name(raw_name)
                if not food_name or len(food_name) < 2: continue
                if any(p in food_name for p in PROCESSED_COOKING_PATTERNS):
                    if food_name not in self.food_names:
                        self.food_names.add(food_name)
                        self.stats['processed_db_included'] += 1
                else:
                    self.stats['processed_db_excluded_pattern'] += 1

    def save_results(self) -> None:
        sorted_names = sorted(self.food_names, key=len, reverse=True)
        with open(os.path.join(DATA_DIR, 'food_names.txt'), 'w', encoding='utf-8') as f:
            f.write('\n'.join(sorted_names) + '\n')
        print(f"💾 음식명 목록 저장 완료: {len(sorted_names)}개")

    def build_and_save_normalization_dict(self) -> None:
        norm_dict = build_normalization_dictionary(self.food_names)
        with open(os.path.join(DATA_DIR, 'menu_normalization.json'), 'w', encoding='utf-8') as f:
            json.dump(norm_dict, f, ensure_ascii=False, indent=2)
        print(f"💾 메뉴 정규화 사전 저장 완료")

    def integrate(self) -> None:
        print("=" * 70)
        print("🍽️ 음식 DB 통합 전처리 (SSOT 적용)")
        print("=" * 70)
        self.load_food_db(self._find_file('*음식DB*.csv'))
        self.load_processed_db(self._find_file('*가공식품*.csv'))
        self.save_results()
        self.build_and_save_normalization_dict()
        print(f"🎉 총 고유 음식명: {len(self.food_names):,}개")

if __name__ == '__main__':
    FoodDBIntegrator().integrate()