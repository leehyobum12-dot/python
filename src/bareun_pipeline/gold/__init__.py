"""
Gold Layer 패키지
==================
DB 스키마 매칭, 파일 출력, Supabase 적재를 담당합니다.

즉시 노출 (가벼운 모듈):
- transform: GoldRestaurant Pydantic 모델
- export: 파일 출력

Lazy 노출 (무거운 모듈):
- supabase_loader: supabase 클라이언트
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any
import importlib

# 가벼운 모듈은 즉시 노출
from .transform import GoldTransformer, GoldRestaurant
from .export import GoldExporter, ExportResult

if TYPE_CHECKING:
    from .supabase_loader import SupabaseLoader, load_records


_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    'SupabaseLoader': ('bareun_pipeline.gold.supabase_loader', 'SupabaseLoader'),
    'load_records': ('bareun_pipeline.gold.supabase_loader', 'load_records'),
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
    'load_records',
]