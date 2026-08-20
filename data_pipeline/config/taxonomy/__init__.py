# data_pipeline/config/taxonomy/__init__.py
"""
[SSOT-1] 비즈니스 온톨로지 — 7차원 카테고리 분류
소비자: classifier.py, 프론트엔드 UI 필터, DB 인덱싱
"""
from .cuisine import CuisineTaxonomy
from .ingredient import IngredientTaxonomy
from .cooking import CookingTaxonomy
from .nutrient import NutrientTaxonomy
from .regional import RegionalTaxonomy
from .allergen import AllergenTaxonomy
from .occasion import OccasionTaxonomy

ALL_TAXONOMIES = {
    'cuisine': CuisineTaxonomy,
    'ingredient': IngredientTaxonomy,
    'cooking': CookingTaxonomy,
    'nutrient': NutrientTaxonomy,
    'regional': RegionalTaxonomy,
    'allergen': AllergenTaxonomy,
    'occasion': OccasionTaxonomy,
}

__all__ = [
    'CuisineTaxonomy', 'IngredientTaxonomy', 'CookingTaxonomy',
    'NutrientTaxonomy', 'RegionalTaxonomy', 'AllergenTaxonomy',
    'OccasionTaxonomy', 'ALL_TAXONOMIES',
]