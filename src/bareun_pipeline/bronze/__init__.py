"""
Bronze Layer 패키지
====================
원본 데이터 수집과 보존을 담당합니다.

즉시 노출 (가벼운 모듈):
- raw_archiver: 원본 데이터 보존 (NDJSON)
- tourist_checkpoint: 관광공사 체크포인트

Lazy 노출 (무거운 모듈):
- collect: 수집 orchestration (ThreadPoolExecutor, QuotaManager 등 무거운 의존성)
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any
import importlib

# 가벼운 모듈은 즉시 노출
from .raw_archiver import RawArchiver
from .tourist_checkpoint import TouristCheckpoint

if TYPE_CHECKING:
    from .collect import BronzeCollector

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    'BronzeCollector': ('bareun_pipeline.bronze.collect', 'BronzeCollector'),
}

def __getattr__(name: str) -> Any:
    """PEP 562 기반 모듈 레벨 Lazy Import"""
    if name in _LAZY_IMPORTS:
        module_path, attr_name = _LAZY_IMPORTS[name]
        module = importlib.import_module(module_path)
        attr = getattr(module, attr_name)
        globals()[name] = attr  # 캐싱하여 재호출 시 즉시 반환
        return attr
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

def __dir__() -> list[str]:
    return ['RawArchiver', 'TouristCheckpoint'] + list(_LAZY_IMPORTS.keys())

__all__ = [
    'RawArchiver',
    'TouristCheckpoint',
    'BronzeCollector',
]