"""
NLP 패키지
===========
자연어처리 모듈입니다.

주의: 이 패키지의 모듈은 torch, transformers 등 무거운 의존성을 포함합니다.
실제로 사용할 때만 import되도록 lazy loading을 적용합니다.

사용법 (lazy):
    from bareun_pipeline.nlp import MenuNERPredictor  # 이 시점에 torch 로드

사용법 (직접):
    from bareun_pipeline.nlp.ner.predictor import MenuNERPredictor  # 동일

내부 구조 은닉:
    하위 모듈 경로는 리팩터링 시 변경될 수 있습니다.
    패키지 레벨 import를 사용하면 리팩터링에 영향을 받지 않습니다.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

# Pylance/Pyright 타입 검사용 (런타임에는 실행되지 않음)
if TYPE_CHECKING:
    from .hybrid import HybridMenuExtractor
    from .llm.verifier import LLMVerifier
    from .ner.pipeline import NERMenuPipeline
    from .ner.postprocessor import NERPostprocessor
    from .ner.predictor import MenuNERPredictor


# ─────────────── Lazy Loading (PEP 562) ───────────────
# 무거운 의존성(torch, transformers)은 실제로 접근할 때만 로드

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    'MenuNERPredictor': ('bareun_pipeline.nlp.ner.predictor', 'MenuNERPredictor'),
    'NERPostprocessor': ('bareun_pipeline.nlp.ner.postprocessor', 'NERPostprocessor'),
    'NERMenuPipeline': ('bareun_pipeline.nlp.ner.pipeline', 'NERMenuPipeline'),
    'LLMVerifier': ('bareun_pipeline.nlp.llm.verifier', 'LLMVerifier'),
    'HybridMenuExtractor': ('bareun_pipeline.nlp.hybrid', 'HybridMenuExtractor'),
}


def __getattr__(name: str) -> Any:
    """모듈 레벨 lazy import (PEP 562)"""
    if name in _LAZY_IMPORTS:
        module_path, attr_name = _LAZY_IMPORTS[name]
        module = importlib.import_module(module_path)
        attr = getattr(module, attr_name)
        # 최초 로드 후 모듈 전역에 캐싱하여 재import 오버헤드 방지
        globals()[name] = attr
        return attr
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


def __dir__() -> list[str]:
    """dir() 및 런타임 자동완성 지원"""
    return list(_LAZY_IMPORTS.keys())


# 정적 분석기(Pylance)가 인식할 수 있도록 리터럴 리스트로 명시
__all__ = [
    'HybridMenuExtractor',
    'LLMVerifier',
    'MenuNERPredictor',
    'NERMenuPipeline',
    'NERPostprocessor',
]
