"""
Silver Layer 패키지
====================
데이터 정규화, 병합, 보강, 분류를 담당합니다.

즉시 노출 (가벼운 모듈):
- normalize: 정규화
- merge: 병합

Lazy 노출 (무거운 모듈):
- classify: Aho-Corasick 분류 (pyahocorasick C 확장)
- enrich: 지오코딩 (httpx)
- menu_extract: NER + LLM (torch, transformers)
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

from .merge import Merger, merge_records

# 가벼운 모듈은 즉시 노출
from .normalize import Normalizer, normalize_records

# Pylance/Pyright 타입 검사용 (런타임에는 실행되지 않음)
if TYPE_CHECKING:
    from .classify import TaxonomyClassifier, classify_text, get_classifier
    from .enrich import SilverEnricher
    from .menu_extract import MenuExtractor


# ─────────────── Lazy Loading (PEP 562) ───────────────

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    'TaxonomyClassifier': ('bareun_pipeline.silver.classify', 'TaxonomyClassifier'),
    'classify_text': ('bareun_pipeline.silver.classify', 'classify_text'),
    'get_classifier': ('bareun_pipeline.silver.classify', 'get_classifier'),
    'SilverEnricher': ('bareun_pipeline.silver.enrich', 'SilverEnricher'),
    'MenuExtractor': ('bareun_pipeline.silver.menu_extract', 'MenuExtractor'),
}


def __getattr__(name: str) -> Any:
    """모듈 레벨 lazy import (PEP 562)"""
    if name in _LAZY_IMPORTS:
        module_path, attr_name = _LAZY_IMPORTS[name]
        module = importlib.import_module(module_path)
        attr = getattr(module, attr_name)
        globals()[name] = attr
        return attr
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


def __dir__() -> list[str]:
    """dir() 및 런타임 자동완성 지원"""
    immediate = ['Normalizer', 'normalize_records', 'Merger', 'merge_records']
    return immediate + list(_LAZY_IMPORTS.keys())


# 정적 분석기(Pylance)가 인식할 수 있도록 리터럴 리스트로 명시
__all__ = [
    'MenuExtractor',
    'Merger',
    'Normalizer',
    'SilverEnricher',
    'TaxonomyClassifier',
    'classify_text',
    'get_classifier',
    'merge_records',
    'normalize_records',
]
