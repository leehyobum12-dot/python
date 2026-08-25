"""
음식 DB 통합 전처리 + 메뉴 정규화 사전 생성
=============================================
- 식품 DB, 가공식품 DB 로드
- 메뉴명 3층 구조 분해 (재료 + 수식어 + 조리법)
- 메뉴 정규화 사전 생성

기존 참고:
- ner/preprocessing/food_db_integrator.py 전체 구조
- decompose_menu_name() 3층 구조 분해
- build_normalization_dictionary() 정규화 사전 생성

보존 사항:
- 음식 DB 통합 로직
- 메뉴 정규화 사전 생성
- 카테고리 제외 로직

개선 사항:
- 구조화 로깅
- 타입 안전성 강화
"""

from __future__ import annotations

import csv
import glob
import json
import os
import re
from typing import TypedDict

from ..domain.constants import (
    COOKING_SUFFIXES,
    EXCLUDE_CATEGORIES,
    INGREDIENT_NOUNS,
    MODIFIER_PREFIXES,
    PROCESSED_EXCLUDE_KEYWORDS,
)
from ..observability.logging import get_logger
from ..settings import get_settings

logger = get_logger(__name__)


class DecomposeResult(TypedDict):
    """메뉴 분해 결과"""

    original: str
    ingredients: list[str]
    modifiers: list[str]
    cooking: str | None
    base_menu: str | None


class NormalizationDict(TypedDict):
    """정규화 사전 구조"""

    version: str
    base_menus: dict[str, dict]
    stats: dict[str, float]


def decompose_menu_name(name: str) -> DecomposeResult:
    """
    메뉴명을 3층 구조로 분해

    기존 ner/preprocessing/food_db_integrator.py의 decompose_menu_name() 함수 보존

    Args:
        name: 메뉴명

    Returns:
        분해 결과
    """
    result: DecomposeResult = {
        'original': name,
        'ingredients': [],
        'modifiers': [],
        'cooking': None,
        'base_menu': None,
    }
    remaining = name

    # 조리법 접미사 추출
    for suffix in sorted(COOKING_SUFFIXES, key=len, reverse=True):
        if remaining.endswith(suffix):
            result['cooking'] = suffix
            remaining = remaining[: -len(suffix)]
            break

    # 수식어 접두사 추출
    changed = True
    while changed:
        changed = False
        for prefix in MODIFIER_PREFIXES:
            if remaining.startswith(prefix):
                result['modifiers'].append(prefix)
                remaining = remaining[len(prefix) :]
                changed = True
                break

    # 재료 추출
    for noun in INGREDIENT_NOUNS:
        if noun in remaining:
            result['ingredients'].append(noun)

    # 기본 메뉴 생성
    base_parts = result['ingredients'] + ([result['cooking']] if result['cooking'] else [])
    result['base_menu'] = ''.join(base_parts) if base_parts else name

    return result


def build_normalization_dictionary(food_names: set[str]) -> NormalizationDict:
    """
    메뉴 정규화 사전 생성

    기존 ner/preprocessing/food_db_integrator.py의 build_normalization_dictionary() 함수 보존

    Args:
        food_names: 음식명 집합

    Returns:
        정규화 사전
    """
    base_menus: dict[str, dict] = {}

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

    # 정렬
    for base in base_menus:
        base_menus[base]['variants'].sort(key=len, reverse=True)
        base_menus[base]['cooking_methods'] = sorted(base_menus[base]['cooking_methods'])
        base_menus[base]['modifiers'] = sorted(base_menus[base]['modifiers'])
        base_menus[base]['ingredients'] = sorted(base_menus[base]['ingredients'])

    stats = {
        'total_base_menus': len(base_menus),
        'total_variants': sum(len(b['variants']) for b in base_menus.values()),
        'avg_variants_per_base': sum(len(b['variants']) for b in base_menus.values())
        / len(base_menus)
        if base_menus
        else 0,
        'max_variants': max(len(b['variants']) for b in base_menus.values()) if base_menus else 0,
    }

    return {'version': '2.0', 'base_menus': base_menus, 'stats': stats}


class FoodDBIntegrator:
    """
    음식 DB 통합기

    사용법:
        integrator = FoodDBIntegrator()
        integrator.integrate()
    """

    def __init__(self):
        self.settings = get_settings()
        self.food_names: set[str] = set()
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
        self.excluded_category_counts: dict[str, int] = {}

    def _find_file(self, pattern: str) -> str | None:
        """파일 자동 탐색"""
        search_patterns = [
            os.path.join(self.settings.data_dir, pattern),
            os.path.join(self.settings.data_dir, '**', pattern),
        ]
        for sp in search_patterns:
            matches = glob.glob(sp, recursive=True)
            if matches:
                return sorted(matches, key=os.path.getsize, reverse=True)[0]
        return None

    def _clean_food_name(self, name: str) -> str:
        """음식명 정제"""
        if not name:
            return ''
        name = re.sub(
            r'\s*\([LRMSXFPG]+\)|\s*\(Tall\)|\(Grande\)|\(Venti\)|\(Mini Venti\)', '', name
        )
        name = re.sub(
            r'\s*\(초대용량\)|\(빅사이즈\)|\(1인\)|\(2인\)|\s*\(Solo\)|\(Double\)|\(Triple\)',
            '',
            name,
        )
        name = re.sub(r'\s*아이스\s*\(ICED\)|\s*핫\s*\(HOT\)|\s*ICED|\s*HOT', '', name)
        name = re.sub(r'[^\uAC00-\uD7A3a-zA-Z0-9\s]', '', name)
        return re.sub(r'\s+', ' ', name).strip()

    def _extract_category(self, raw_name: str) -> str | None:
        """카테고리 추출"""
        return raw_name.split('_', 1)[0].strip() if '_' in raw_name else None

    def _extract_food_name(self, raw_name: str) -> str:
        """음식명 추출"""
        name = raw_name.split('_', 1)[1] if '_' in raw_name else raw_name
        return self._clean_food_name(name)

    def _normalize_category(self, category: str) -> str:
        """카테고리 정규화"""
        return re.sub(r'[()/\s]', '', category).lower() if category else ''

    def _is_excluded_category(self, category: str | None) -> bool:
        """제외 카테고리 여부"""
        if not category:
            return False
        normalized = self._normalize_category(category)
        for excluded in EXCLUDE_CATEGORIES:
            excluded_norm = excluded.lower()
            if normalized == excluded_norm or (
                excluded_norm in normalized and len(excluded_norm) >= 3
            ):
                return True
        return False

    def load_food_db(self, path: str | None) -> None:
        """음식 DB 로드"""
        if not path or not os.path.exists(path):
            logger.warning('food_db_not_found', path=path)
            return

        logger.info('food_db_loading', path=path)

        with open(path, encoding='utf-8-sig', newline='') as f:
            for row in csv.DictReader(f):
                raw_name = (
                    row.get('식품명', '') or row.get('food_name', '') or row.get('대표식품명', '')
                )
                if not raw_name:
                    continue
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

    def load_processed_db(self, path: str | None) -> None:
        """가공식품 DB 로드"""
        if not path or not os.path.exists(path):
            logger.warning('processed_db_not_found', path=path)
            return

        logger.info('processed_db_loading', path=path)

        with open(path, encoding='utf-8-sig', newline='') as f:
            for row in csv.DictReader(f):
                raw_name = row.get('식품명', '') or row.get('food_name', '')
                if not raw_name:
                    continue
                self.stats['processed_db_total'] += 1

                if any(kw in raw_name for kw in PROCESSED_EXCLUDE_KEYWORDS):
                    self.stats['processed_db_excluded_keyword'] += 1
                    continue

                food_name = self._clean_food_name(raw_name)
                if not food_name or len(food_name) < 2:
                    continue

                if any(p in food_name for p in COOKING_SUFFIXES):
                    if food_name not in self.food_names:
                        self.food_names.add(food_name)
                        self.stats['processed_db_included'] += 1
                else:
                    self.stats['processed_db_excluded_pattern'] += 1

    def save_results(self) -> None:
        """음식명 목록 저장"""
        sorted_names = sorted(self.food_names, key=len, reverse=True)
        output_path = os.path.join(self.settings.data_dir, 'food_names.txt')

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(sorted_names) + '\n')

        logger.info(
            'food_names_saved',
            count=len(sorted_names),
            path=output_path,
        )

    def build_and_save_normalization_dict(self) -> None:
        """정규화 사전 생성 및 저장"""
        norm_dict = build_normalization_dictionary(self.food_names)
        output_path = os.path.join(self.settings.data_dir, 'menu_normalization.json')

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(norm_dict, f, ensure_ascii=False, indent=2)

        logger.info(
            'normalization_dict_saved',
            stats=norm_dict['stats'],
            path=output_path,
        )

    def integrate(self) -> None:
        """음식 DB 통합 실행"""
        logger.info('food_db_integration_start')

        self.load_food_db(self._find_file('*음식DB*.csv'))
        self.load_processed_db(self._find_file('*가공식품*.csv'))
        self.save_results()
        self.build_and_save_normalization_dict()

        logger.info(
            'food_db_integration_complete',
            total_food_names=len(self.food_names),
            stats=self.stats,
        )
