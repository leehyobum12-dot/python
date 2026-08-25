"""
NER 서브패키지
===============
Named Entity Recognition (개체명 인식) 모듈입니다.

모듈:
- predictor: KLUE-RoBERTa 추론
- postprocessor: 8단계 다층 검증
- pipeline: NER 파이프라인 (실전 로깅)

주의: 이 패키지의 모듈은 torch, transformers 등 무거운 의존성을 포함합니다.
실제로 사용할 때만 import되도록 lazy loading을 적용합니다.

사용법 (lazy):
    from bareun_pipeline.nlp.ner import MenuNERPredictor

사용법 (직접):
    from bareun_pipeline.nlp.ner.predictor import MenuNERPredictor
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .pipeline import NERMenuPipeline
    from .postprocessor import NERPostprocessor
    from .predictor import MenuNERPredictor


_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    'MenuNERPredictor': ('bareun_pipeline.nlp.ner.predictor', 'MenuNERPredictor'),
    'NERPostprocessor': ('bareun_pipeline.nlp.ner.postprocessor', 'NERPostprocessor'),
    'NERMenuPipeline': ('bareun_pipeline.nlp.ner.pipeline', 'NERMenuPipeline'),
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
    return list(_LAZY_IMPORTS.keys())


__all__ = [
    'MenuNERPredictor',
    'NERMenuPipeline',
    'NERPostprocessor',
]
