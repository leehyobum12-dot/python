"""
어댑터 패키지
==============
공공데이터 원본 수집을 담당하는 어댑터 모듈입니다.

즉시 노출 (가벼운 모듈):
- base: BaseAdapter 추상 클래스 + 주소 유틸리티

Lazy 노출 (무거운 모듈):
- 각 어댑터 클래스 (외부 API 클라이언트 및 무거운 매핑 로직 포함)
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

# 가벼운 Base 클래스 및 유틸리티는 즉시 노출
from .base import (
    BaseAdapter,
    CircuitBreaker,
    CircuitOpenError,
    canon_province,
    create_session,
    extract_emd_from_addr,
    is_emd_token,
    is_road_name,
    match_prefixes,
    norm_addr,
    norm_name,
    parse_addr_tokens,
)

if TYPE_CHECKING:
    from .food_safety import FoodSafetyAdapter
    from .general_restaurant import GeneralRestaurantAdapter
    from .low_sodium import LowSodiumAdapter
    from .model_restaurant import ModelRestaurantAdapter
    from .safe_restaurant import SafeRestaurantAdapter
    from .tourist_info import TouristInfoAdapter

_LAZY_ADAPTERS: dict[str, tuple[str, str]] = {
    'SafeRestaurantAdapter': ('bareun_pipeline.adapters.safe_restaurant', 'SafeRestaurantAdapter'),
    'LowSodiumAdapter': ('bareun_pipeline.adapters.low_sodium', 'LowSodiumAdapter'),
    'FoodSafetyAdapter': ('bareun_pipeline.adapters.food_safety', 'FoodSafetyAdapter'),
    'ModelRestaurantAdapter': (
        'bareun_pipeline.adapters.model_restaurant',
        'ModelRestaurantAdapter',
    ),
    'TouristInfoAdapter': ('bareun_pipeline.adapters.tourist_info', 'TouristInfoAdapter'),
    'GeneralRestaurantAdapter': (
        'bareun_pipeline.adapters.general_restaurant',
        'GeneralRestaurantAdapter',
    ),
}


def __getattr__(name: str) -> Any:
    """PEP 562 기반 어댑터 Lazy Import"""
    if name in _LAZY_ADAPTERS:
        module_path, attr_name = _LAZY_ADAPTERS[name]
        module = importlib.import_module(module_path)
        attr = getattr(module, attr_name)
        globals()[name] = attr
        return attr
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


def __dir__() -> list[str]:
    base_utils = [
        'BaseAdapter',
        'CircuitBreaker',
        'CircuitOpenError',
        'create_session',
        'parse_addr_tokens',
        'canon_province',
        'match_prefixes',
        'extract_emd_from_addr',
        'is_road_name',
        'is_emd_token',
        'norm_name',
        'norm_addr',
        'get_all_adapters',
    ]
    return base_utils + list(_LAZY_ADAPTERS.keys())


def get_all_adapters() -> list[type]:
    """
    모든 어댑터 클래스 목록 반환 (Lazy Load)
    이 함수가 호출되는 시점에 각 어댑터 모듈이 메모리에 로드됩니다.
    """
    return [__getattr__(name) for name in _LAZY_ADAPTERS]


__all__ = [
    # Base utilities
    'BaseAdapter',
    'CircuitBreaker',
    'CircuitOpenError',
    'create_session',
    'parse_addr_tokens',
    'canon_province',
    'match_prefixes',
    'extract_emd_from_addr',
    'is_road_name',
    'is_emd_token',
    'norm_name',
    'norm_addr',
    'get_all_adapters',
    # Lazy Adapters
    'SafeRestaurantAdapter',
    'LowSodiumAdapter',
    'FoodSafetyAdapter',
    'ModelRestaurantAdapter',
    'TouristInfoAdapter',
    'GeneralRestaurantAdapter',
]
