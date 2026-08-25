"""
재학습 관리 (합성 + 실전 혼합)
================================
- 합성 데이터 + 실전 데이터 혼합
- 실전 데이터 업샘플링
- 라벨 분포 확인

기존 참고:
- ner/training/retrain_manager.py 전체 구조
- 합성 80% + 실전 20% 혼합 비율
- 실전 데이터 5배 업샘플링

보존 사항:
- 혼합 비율
- 업샘플링
- 라벨 분포 확인

개선 사항:
- 구조화 로깅
- 타입 안전성 강화
"""

from __future__ import annotations

import random

from ..observability.logging import get_logger

logger = get_logger(__name__)


def mix_datasets(
    synthetic_train: list[dict],
    synthetic_val: list[dict],
    real_data: list[dict],
    synthetic_ratio: float = 0.8,
    upsample_factor: int = 5,
) -> dict[str, list[dict]]:
    """
    합성 + 실전 데이터 혼합

    기존 ner/training/retrain_manager.py의 main() 함수 보존

    Args:
        synthetic_train: 합성 학습 데이터
        synthetic_val: 합성 검증 데이터
        real_data: 실전 데이터
        synthetic_ratio: 합성 데이터 비율
        upsample_factor: 실전 데이터 업샘플링 배수

    Returns:
        {'train': [...], 'val': [...], 'test': [...]}
    """
    logger.info(
        'mix_datasets_start',
        synthetic_train=len(synthetic_train),
        synthetic_val=len(synthetic_val),
        real_data=len(real_data),
    )

    if not real_data:
        logger.warning('no_real_data')
        return {
            'train': synthetic_train,
            'val': synthetic_val,
            'test': [],
        }

    # 실전 데이터 업샘플링 (영향력 확보)
    real_upsampled = real_data * upsample_factor
    logger.info(
        'real_data_upsampled',
        original=len(real_data),
        upsampled=len(real_upsampled),
        factor=upsample_factor,
    )

    # train 혼합 (합성 80% + 실전 20%)
    syn_train_sample = random.sample(synthetic_train, int(len(synthetic_train) * synthetic_ratio))
    mixed_train = syn_train_sample + real_upsampled
    random.shuffle(mixed_train)

    # val: 합성 val + 실전 10%
    real_val_count = max(1, int(len(real_data) * 0.1))
    real_val = random.sample(real_data, min(real_val_count, len(real_data)))
    mixed_val = synthetic_val + real_val
    random.shuffle(mixed_val)

    # test: 실전 5%
    real_test = real_data[: max(1, int(len(real_data) * 0.05))]

    logger.info(
        'mix_datasets_complete',
        train=len(mixed_train),
        val=len(mixed_val),
        test=len(real_test),
    )

    return {
        'train': mixed_train,
        'val': mixed_val,
        'test': real_test,
    }
