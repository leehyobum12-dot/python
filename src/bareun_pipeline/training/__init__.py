"""
Training 패키지
================
NER 모델 학습을 위한 모듈입니다.

주의: 이 패키지의 모듈은 datasets, transformers, torch 등
매우 무거운 의존성을 포함합니다.
실제로 사용할 때만 import되도록 lazy loading을 적용합니다.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any
import importlib

if TYPE_CHECKING:
    from .data_generator import generate_dataset, save_dataset
    from .label_real_data import is_valid_menu, tokenize_and_label
    from .verify_labels import verify_sample
    from .retrain_manager import mix_datasets
    from .trainer import train_ner_model


_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    'generate_dataset': ('bareun_pipeline.training.data_generator', 'generate_dataset'),
    'save_dataset': ('bareun_pipeline.training.data_generator', 'save_dataset'),
    'is_valid_menu': ('bareun_pipeline.training.label_real_data', 'is_valid_menu'),
    'tokenize_and_label': ('bareun_pipeline.training.label_real_data', 'tokenize_and_label'),
    'verify_sample': ('bareun_pipeline.training.verify_labels', 'verify_sample'),
    'mix_datasets': ('bareun_pipeline.training.retrain_manager', 'mix_datasets'),
    'train_ner_model': ('bareun_pipeline.training.trainer', 'train_ner_model'),
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
    'generate_dataset',
    'save_dataset',
    'is_valid_menu',
    'tokenize_and_label',
    'verify_sample',
    'mix_datasets',
    'train_ner_model',
]