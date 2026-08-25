"""
LLM 서브패키지
===============
Large Language Model 검증기 모듈입니다.

모듈:
- verifier: Ollama 기반 LLM 검증기 (Few-shot + RAG)

주의: 이 패키지의 모듈은 httpx를 사용합니다.
실제로 사용할 때만 import되도록 lazy loading을 적용합니다.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .verifier import LLMVerifier


_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    'LLMVerifier': ('bareun_pipeline.nlp.llm.verifier', 'LLMVerifier'),
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
    'LLMVerifier',
]
