"""
합성 NER 데이터 생성기
========================
- food_names.txt를 활용해서 NER 학습 데이터 생성
- 긍정 템플릿 (메뉴 포함) + 부정 템플릿 (메뉴 없음)

기존 참고:
- ner/preprocessing/ner_data_generator.py 전체 구조
- POSITIVE_TEMPLATES, NEGATIVE_TEMPLATES
- BIO 태깅 토큰화

보존 사항:
- 합성 데이터 템플릿
- BIO 태깅 변환
- 데이터 분할 (train/val/test)

개선 사항:
- 구조화 로깅
- 타입 안전성 강화
"""
from __future__ import annotations

import json
import os
import random
from typing import List, Dict, Tuple, Optional

from ..observability.logging import get_logger


logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════
# 긍정 템플릿 (메뉴 포함)
# ═══════════════════════════════════════════════════════════
POSITIVE_TEMPLATES = [
    '{store}에서 {menu}를 먹었어요',
    '{store}의 {menu}가 진짜 맛있어요',
    '{menu}는 {store}의 대표메뉴에요',
    '{store}에 가면 꼭 {menu}를 드세요',
    '{store} {menu} 추천합니다',
    '{menu} 먹으러 {store} 갔어요',
    '{store}에서 {menu} 주문했어요',
    '{store}의 시그니처는 {menu}에요',
    '{menu} 맛집 {store} 다녀왔어요',
    '{store} {menu} 가격도 저렴해요',
    '{store}에서 {menu}랑 다른 메뉴도 시켰어요',
    '{menu}가 유명한 {store} 방문 후기',
    '{store} 대표메뉴 {menu} 솔직 리뷰',
    '{store}의 인기 메뉴는 {menu}입니다',
    '{menu} 먹으로 {store} 또 갈거에요',
    '{store}에서 {menu}를 포장해왔어요',
    '{menu}가 맛있는 {store} 찾았어요',
    '{store} 가면 {menu} 꼭 시키세요',
    '{store}의 {menu} 양도 많고 맛있어요',
    '{menu} 전문점 {store} 후기',
    '오늘 {store}에서 {menu} 먹었는데 최고였어요',
    '{store} {menu}는 꼭 드셔보세요',
    '{menu} 때문에 {store} 자주 가요',
    '{store} 가면 {menu}는 필수에요',
    '{menu} 먹으로 {store} 또 방문',
]

# ═══════════════════════════════════════════════════════════
# 부정 템플릿 (메뉴 없음)
# ═══════════════════════════════════════════════════════════
NEGATIVE_TEMPLATES = [
    '{store}에 다녀왔어요',
    '{store} 분위기가 좋아요',
    '{store} 서비스가 친절해요',
    '{store} 위치가 좋아요',
    '{store} 인테리어가 예뻐요',
    '{store} 주차가 편해요',
    '{store} 웨이팅이 있었어요',
    '{store} 예약하고 갔어요',
    '{store}에서 식사했어요',
    '{store} 또 가고 싶어요',
    '{store} 재방문 의사 있어요',
    '{store} 친구랑 갔는데 좋았어요',
    '{store} 가족끼리 가기 좋아요',
    '{store} 데이트 코스로 추천',
    '{store} 회식하기 좋았어요',
]

# ═══════════════════════════════════════════════════════════
# 가게 이름 생성기
# ═══════════════════════════════════════════════════════════
STORE_LOCATIONS = [
    '제주', '서울', '부산', '전주', '강릉', '속초', '여수', '통영',
    '대구', '광주', '인천', '수원', '성남', '청주', '천안', '목포',
    '춘천', '경주', '안동', '공주', '해남', '보성', '담양', '순천',
]

STORE_SUFFIXES = [
    '식당', '전문점', '집', '맛집', '회관', '밥집', '국밥집',
    '식당', '전문점', '집',  # 가중치
]

FOOD_KEYWORDS = [
    '흑돼지', '해장국', '국수', '비빔밥', '초밥', '갈비', '불고기',
    '찌개', '구이', '탕', '전골', '회', '칼국수', '냉면',
    '떡볶이', '치킨', '삼겹살', '족발', '보쌈', '순대',
]

REAL_STORE_NAMES = [
    '올래국수', '우진해장국', '자매국수', '삼대국수회관',
    '돈사돈', '흑돈가', '셰프와흑돼지', '할매국밥',
    '하동관', '신선설농탕', '모슬포항식당', '진짜원조초밥',
    '전주비빔밥명가', '강릉초당두부', '여수한정식', '통영충무김밥',
    '부산돼지국밥', '대구막창', '광주상추튀김', '제주흑돼지전문점',
    '명동교자', '을지로국밥', '남포면옥', '평양냉면집',
    '진주회관', '하림각', '양평해장국', '이화수전통육개장',
]


def generate_store_name() -> str:
    """실제 스타일 가게 이름 생성"""
    # 40%는 실제 가게 이름
    if random.random() < 0.4:
        return random.choice(REAL_STORE_NAMES)

    location = random.choice(STORE_LOCATIONS)
    suffix = random.choice(STORE_SUFFIXES)

    # 50% 확률로 음식 키워드 추가
    if random.random() < 0.5:
        food_word = random.choice(FOOD_KEYWORDS)
        return f'{location}{food_word}{suffix}'

    return f'{location}{suffix}'


def tokenize_with_labels(
    pattern: str,
    store: str,
    menu: Optional[str] = None
) -> Tuple[List[str], List[str]]:
    """
    템플릿 패턴을 토큰과 BIO 라벨로 변환

    기존 ner/preprocessing/ner_data_generator.py의 tokenize_with_labels() 함수 보존

    Args:
        pattern: 템플릿 패턴
        store: 가게명
        menu: 메뉴명 (없으면 None)

    Returns:
        (토큰 목록, 라벨 목록)
    """
    tokens: List[str] = []
    labels: List[str] = []

    # 플레이스홀더로 분할
    import re
    parts = re.split(r'(\{store\}|\{menu\})', pattern)

    for part in parts:
        if not part:
            continue

        if part == '{store}':
            # 가게 이름 토큰 (공백으로 분리)
            store_tokens = store.split()
            for i, tok in enumerate(store_tokens):
                tokens.append(tok)
                labels.append('B-STORE' if i == 0 else 'I-STORE')

        elif part == '{menu}':
            # 메뉴 이름 토큰 (공백으로 분리)
            if menu:
                menu_tokens = menu.split()
                for i, tok in enumerate(menu_tokens):
                    tokens.append(tok)
                    labels.append('B-MENU' if i == 0 else 'I-MENU')

        else:
            # 일반 텍스트 토큰
            for tok in part.split():
                tokens.append(tok)
                labels.append('O')

    return tokens, labels


def generate_dataset(
    target_samples: int,
    food_names: List[str],
    positive_ratio: float = 0.8,
) -> List[Dict]:
    """
    합성 NER 데이터셋 생성

    기존 ner/preprocessing/ner_data_generator.py의 generate_dataset() 함수 보존

    Args:
        target_samples: 목표 샘플 수
        food_names: 음식명 목록
        positive_ratio: 긍정 샘플 비율

    Returns:
        생성된 데이터셋
    """
    dataset: List[Dict] = []

    # 긍정 샘플 (메뉴 포함)
    positive_count = int(target_samples * positive_ratio)
    logger.info('generating_positive_samples', count=positive_count)

    for _ in range(positive_count):
        store = generate_store_name()
        menu = random.choice(food_names)
        template = random.choice(POSITIVE_TEMPLATES)

        tokens, labels = tokenize_with_labels(template, store, menu)
        dataset.append({'tokens': tokens, 'labels': labels})

    # 부정 샘플 (메뉴 없음)
    negative_count = target_samples - positive_count
    logger.info('generating_negative_samples', count=negative_count)

    for _ in range(negative_count):
        store = generate_store_name()
        template = random.choice(NEGATIVE_TEMPLATES)

        tokens, labels = tokenize_with_labels(template, store, None)
        dataset.append({'tokens': tokens, 'labels': labels})

    return dataset


def split_dataset(
    dataset: List[Dict],
    train_ratio: float = 0.8,
    val_ratio: float = 0.1,
) -> Tuple[List[Dict], List[Dict], List[Dict]]:
    """
    데이터셋 분할 (train/val/test)

    Args:
        dataset: 전체 데이터셋
        train_ratio: 학습 데이터 비율
        val_ratio: 검증 데이터 비율

    Returns:
        (train, val, test)
    """
    random.shuffle(dataset)
    n = len(dataset)
    train_end = int(n * train_ratio)
    val_end = int(n * (train_ratio + val_ratio))

    train = dataset[:train_end]
    val = dataset[train_end:val_end]
    test = dataset[val_end:]

    return train, val, test


def save_dataset(
    train: List[Dict],
    val: List[Dict],
    test: List[Dict],
    data_dir: str,
) -> None:
    """
    데이터셋 저장

    Args:
        train: 학습 데이터
        val: 검증 데이터
        test: 테스트 데이터
        data_dir: 저장 디렉토리
    """
    os.makedirs(data_dir, exist_ok=True)

    for name, data in [('train', train), ('val', val), ('test', test)]:
        path = os.path.join(data_dir, f'ner_{name}.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info('dataset_saved', name=name, count=len(data), path=path)