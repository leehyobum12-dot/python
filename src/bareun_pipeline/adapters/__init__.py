"""
어댑터 패키지
==============
공공데이터 원본 수집을 담당하는 어댑터 모듈입니다.

각 어댑터는 BaseAdapter를 상속하며, 다음을 구현해야 합니다:
- source: 어댑터 식별자
- cert_type: 인증 타입
- pii_fields: PII 필드 집합 (마스킹용)
- collect(): 데이터 수집 메서드

모듈:
- base: BaseAdapter 추상 클래스 + 주소 유틸리티
- safe_restaurant: 농식품부 안심식당
- low_sodium: 나트륨 줄이기 실천음식점
- food_safety: 식품안심업소(위생등급)
- model_restaurant: 모범음식점

이 패키지의 모듈은 requests만 사용하므로 가볍습니다.
즉시 노출 방식을 유지합니다.
"""
from __future__ import annotations

from .base import (
    BaseAdapter,
    CircuitBreaker,
    CircuitOpenError,
    create_session,
    parse_addr_tokens,
    canon_province,
    match_prefixes,
    extract_emd_from_addr,
    is_road_name,
    is_emd_token,
    norm_name,
    norm_addr,
)

def get_all_adapters() -> list[type]:
    """
    모든 어댑터 클래스 목록 반환

    지연 import를 사용하여 순환 import 방지

    Returns:
        어댑터 클래스 목록
    """
    from .safe_restaurant import SafeRestaurantAdapter
    from .low_sodium import LowSodiumAdapter
    from .food_safety import FoodSafetyAdapter
    from .model_restaurant import ModelRestaurantAdapter

    return [
        SafeRestaurantAdapter,
        LowSodiumAdapter,
        FoodSafetyAdapter,
        ModelRestaurantAdapter,
    ]

__all__ = [
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