# data_pipeline/config/thresholds.py
"""
[SSOT] 파이프라인 임계값 및 가중치
==================================
하이브리드 파이프라인 튜닝은 이 파일에서만.
"""

# LLM 검증 임계값 (이 미만이면 LLM 호출)
LLM_VERIFY_THRESHOLD = 0.75

# 가게명 유사도 임계값 (이 이상이면 가게명으로 판정)
STORE_SIMILARITY_THRESHOLD = 0.7

# 다차원 신뢰도 가중치
CONFIDENCE_WEIGHTS = {
    'whitelist_match':   0.30,   # 조리법/재료 화이트리스트 매칭
    'store_proximity':   0.20,   # 가게명 10자 이내 근접
    'length_valid':      0.10,   # 메뉴 길이 2~8자
    'no_special_char':   0.10,   # 특수문자 없음
    'multi_menu':        0.10,   # 메뉴 2개 이상 보너스
}

# 메뉴 길이 제한
MENU_MIN_LENGTH = 2
MENU_MAX_LENGTH = 20

# 서브메뉴 최대 개수
MAX_SUB_MENUS = 5

# LLM 프롬프트 텍스트 최대 길이
LLM_TEXT_MAX_LENGTH = 600