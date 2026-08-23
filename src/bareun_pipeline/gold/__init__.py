"""
Gold Layer 패키지 파일: src/bareun_pipeline/gold/__init__.py
==================
DB 스키마 매칭, 파일 출력, Supabase 적재를 담당합니다.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any
import importlib

# 가벼운 모듈은 즉시 노출
from .transform import GoldTransformer, GoldRestaurant
from .export import GoldExporter, ExportResult

if TYPE_CHECKING:
    from .repository import SupabaseLoader, load_to_supabase

# 무거운 모듈은 Lazy로 노출
_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    'SupabaseLoader': ('bareun_pipeline.gold.repository', 'SupabaseLoader'),
    'load_to_supabase': ('bareun_pipeline.gold.repository', 'load_to_supabase'),
}


def __getattr__(name: str) -> Any:
    if name in _LAZY_IMPORTS:
        module_path, attr_name = _LAZY_IMPORTS[name]
        module = importlib.import_module(module_path)
        attr = getattr(module, attr_name)
        globals()[name] = attr
        return attr
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


def __dir__() -> list[str]:
    immediate = ['GoldTransformer', 'GoldRestaurant', 'GoldExporter', 'ExportResult']
    return immediate + list(_LAZY_IMPORTS.keys())


__all__ = [
    'GoldTransformer',
    'GoldRestaurant',
    'GoldExporter',
    'ExportResult',
    'SupabaseLoader',
    'load_to_supabase',
]