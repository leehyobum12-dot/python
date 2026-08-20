# data_pipeline/enrich/ner/preprocessing/ner_data_generator.py
"""
합성 NER 데이터 생성기 (Phase 2)
=================================================
food_names.txt를 활용해서 NER 학습 데이터 생성

입력:
- data/food_names.txt (9,265개 음식명)

출력:
- data/ner_train.json (40,000건)
- data/ner_val.json (5,000건)
- data/ner_test.json (5,000건)

라벨 체계:
- B-STORE, I-STORE: 가게 이름
- B-MENU, I-MENU: 메뉴 이름
- O: 기타

사용법:
    python ner_data_generator.py
"""
import os
import json
import random
import re
from typing import List, Dict, Tuple, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')

# 입력/출력 경로
FOOD_NAMES_PATH = os.path.join(DATA_DIR, 'food_names.txt')
TRAIN_PATH = os.path.join(DATA_DIR, 'ner_train.json')
VAL_PATH = os.path.join(DATA_DIR, 'ner_val.json')
TEST_PATH = os.path.join(DATA_DIR, 'ner_test.json')

# 설정
TARGET_SAMPLES = 50000
POSITIVE_RATIO = 0.8
SEED = 42


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
        return f"{location}{food_word}{suffix}"
    
    return f"{location}{suffix}"


# ═══════════════════════════════════════════════════════════
# BIO 태깅 토큰화
# ═══════════════════════════════════════════════════════════
def tokenize_with_labels(
    pattern: str,
    store: str,
    menu: Optional[str] = None
) -> Tuple[List[str], List[str]]:
    """템플릿 패턴을 토큰과 BIO 라벨로 변환
    
    토큰화 규칙:
    - 공백 기준으로 분리
    - 한글은 음절 단위로 분리하지 않고 단어 단위 유지
    - B-XXX: 엔티티 시작
    - I-XXX: 엔티티 내부
    - O: 기타
    """
    tokens = []
    labels = []
    
    # 플레이스홀더로 분할
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


# ═══════════════════════════════════════════════════════════
# 데이터셋 생성
# ═══════════════════════════════════════════════════════════
def load_food_names() -> List[str]:
    """food_names.txt 로드"""
    if not os.path.exists(FOOD_NAMES_PATH):
        raise FileNotFoundError(f"음식명 파일 없음: {FOOD_NAMES_PATH}")
    
    with open(FOOD_NAMES_PATH, 'r', encoding='utf-8') as f:
        names = [line.strip() for line in f if line.strip()]
    
    print(f"음식명 로드: {len(names)}개")
    return names


def generate_dataset(target_samples: int, food_names: List[str]) -> List[Dict]:
    """합성 NER 데이터셋 생성"""
    dataset = []
    
    # 긍정 샘플 (메뉴 포함)
    positive_count = int(target_samples * POSITIVE_RATIO)
    print(f"긍정 샘플 생성: {positive_count}개")
    for _ in range(positive_count):
        store = generate_store_name()
        menu = random.choice(food_names)
        template = random.choice(POSITIVE_TEMPLATES)
        
        tokens, labels = tokenize_with_labels(template, store, menu)
        dataset.append({'tokens': tokens, 'labels': labels})
    
    # 부정 샘플 (메뉴 없음)
    negative_count = target_samples - positive_count
    print(f"부정 샘플 생성: {negative_count}개")
    for _ in range(negative_count):
        store = generate_store_name()
        template = random.choice(NEGATIVE_TEMPLATES)
        
        tokens, labels = tokenize_with_labels(template, store, None)
        dataset.append({'tokens': tokens, 'labels': labels})
    
    return dataset


# ═══════════════════════════════════════════════════════════
# 분할 및 저장
# ═══════════════════════════════════════════════════════════
def split_dataset(
    dataset: List[Dict],
    train_ratio: float = 0.8,
    val_ratio: float = 0.1
) -> Tuple[List[Dict], List[Dict], List[Dict]]:
    """학습/검증/테스트 분할"""
    random.shuffle(dataset)
    n = len(dataset)
    train_end = int(n * train_ratio)
    val_end = int(n * (train_ratio + val_ratio))
    
    train = dataset[:train_end]
    val = dataset[train_end:val_end]
    test = dataset[val_end:]
    
    return train, val, test


def save_dataset(train, val, test) -> None:
    """데이터셋 저장"""
    os.makedirs(DATA_DIR, exist_ok=True)
    
    for name, data, path in [
        ('train', train, TRAIN_PATH),
        ('val', val, VAL_PATH),
        ('test', test, TEST_PATH)
    ]:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"💾 저장: {path} ({len(data):,}건)")


# ═══════════════════════════════════════════════════════════
# 통계 출력
# ═══════════════════════════════════════════════════════════
def print_stats(dataset: List[Dict]) -> None:
    """라벨 분포 통계"""
    label_counts = {}
    total_tokens = 0
    for sample in dataset:
        for label in sample['labels']:
            label_counts[label] = label_counts.get(label, 0) + 1
            total_tokens += 1
    
    print(f"\n📊 라벨 분포 (총 토큰: {total_tokens:,}):")
    for label, count in sorted(label_counts.items(), key=lambda x: -x[1]):
        pct = count / total_tokens * 100
        print(f"   {label}: {count:,} ({pct:.1f}%)")


def print_samples(dataset: List[Dict], n: int = 5) -> None:
    """샘플 출력"""
    print(f"\n📋 샘플 출력 (긍정):")
    positive_samples = [s for s in dataset if 'B-MENU' in s['labels']]
    for sample in positive_samples[:n]:
        print(f"   토큰: {sample['tokens']}")
        print(f"   라벨: {sample['labels']}")
        print()
    
    print(f"📋 샘플 출력 (부정):")
    negative_samples = [s for s in dataset if 'B-MENU' not in s['labels']]
    for sample in negative_samples[:n]:
        print(f"   토큰: {sample['tokens']}")
        print(f"   라벨: {sample['labels']}")
        print()


# ═══════════════════════════════════════════════════════════
# 메인
# ═══════════════════════════════════════════════════════════
def main():
    print("=" * 60)
    print("🎯 합성 NER 데이터 생성기 (Phase 2)")
    print("=" * 60)
    
    random.seed(SEED)
    
    # 음식명 로드
    food_names = load_food_names()
    
    # 데이터 생성
    dataset = generate_dataset(TARGET_SAMPLES, food_names)
    print(f"\n✅ 총 생성: {len(dataset):,}건")
    
    # 샘플 출력
    print_samples(dataset, n=3)
    
    # 분할
    train, val, test = split_dataset(dataset)
    print(f"📊 분할: train={len(train):,}, val={len(val):,}, test={len(test):,}")
    
    # 저장
    save_dataset(train, val, test)
    
    # 통계
    print_stats(dataset)
    
    print("\n✅ 합성 NER 데이터 생성 완료!")
    print("   다음 단계: NER 모델 학습 (ner_trainer.py)")


if __name__ == '__main__':
    main()