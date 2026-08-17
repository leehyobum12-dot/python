# data_pipeline/enrich/ner/inference/ner_postprocessor.py
"""
NER 후처리 모듈 v4 (화이트리스트 검증 추가)
- 블랙리스트 + 화이트리스트 이중 검증
- 특수문자 포함 단어 제거
- 일반 명사 필터링
"""
import re
from typing import List, Optional


# ═══════════════════════════════════════════════════════════
# 화이트리스트: 메뉴로 유효하려면 아래 중 하나를 포함해야 함
# ═══════════════════════════════════════════════════════════

# 조리법 접미사 (메뉴의 핵심)
VALID_COOKING_SUFFIXES = [
    '구이', '찌개', '전골', '탕', '국', '국밥', '해장국',
    '볶음', '조림', '찜', '무침', '나물', '겉절이', '생채',
    '전', '부침', '튀김', '강정', '탕수', '산적', '적',
    '말이', '꼬치', '회', '초밥', '덮밥', '비빔밥', '볶음밥',
    '김밥', '죽', '국수', '냉면', '막국수', '라면', '우동',
    '소바', '짜장', '짬뽕', '떡볶이', '쫄면', '만두', '순대',
    '스테이크', '파스타', '리조또', '뇨끼', '그라탕', '스프',
    '수프', '샐러드', '샌드위치', '토스트', '베이글', '와플',
    '케이크', '피자', '버거', '카츠', '까스', '커틀릿',
    '볶이', '밥', '면', '솥밥', '오믈렛', '스크램블',
]

# 재료 명사 (메뉴의 재료)
VALID_INGREDIENTS = [
    '고기', '돼지', '소', '쇠', '한우', '닭', '오리', '양',
    '해물', '해산', '새우', '오징어', '문어', '낙지', '주꾸미',
    '갈치', '고등어', '삼치', '전복', '조개', '홍합', '굴',
    '게', '가리비', '연어', '참치', '광어', '우럭', '도미',
    '김치', '된장', '고추장', '간장', '두부', '어묵',
    '버섯', '표고', '느타리', '양송이', '팽이',
    '감자', '고구마', '호박', '애호박', '가지', '오이',
    '당근', '양파', '파', '부추', '미나리', '시금치',
    '배추', '열무', '무', '콩나물', '숙주',
    '계란', '달걀', '치즈', '크림', '버터',
    '갈릭', '마늘', '양념', '매콤', '데리야끼', '로제',
    '흑돼지', '목살', '삼겹', '갈비', '불고기', '제육',
    '명란', '곤드레', '시래기', '고사리', '취나물',
    '바지락', '꼬막', '키조개', '소라', '멍게', '해삼',
]


# ═══════════════════════════════════════════════════════════
# 블랙리스트: 제거 대상
# ═══════════════════════════════════════════════════════════

# 지역명
LOCATION_NAMES = {
    '서울', '부산', '대구', '인천', '광주', '대전', '울산', '세종',
    '제주', '제주시', '서귀포시', '수원', '파주시', '파주',
    '고양시', '고양', '일산', '군포시', '군포', '양주시', '양주',
    '김포시', '김포', '밀양시', '밀양', '통영시', '통영',
    '애월', '한림', '한경', '조천', '구좌', '표선', '남원', '성산',
    '안덕', '대정', '중문', '모슬포', '협재', '함덕', '노형', '연동',
    '명동', '홍대', '강남', '을지로', '종로', '신촌', '역삼',
    '해운대', '서면', '동래', '남포동', '광안리',
    '동성로', '서문시장', '아라동', '이도', '일도', '삼도',
}

# 슬랭/감탄사/형용사
SLANG_BLACKLIST = {
    '존맛', '존맛탱', '꿀맛', '맛집', '완벽', '최고', '강추',
    '굿', '짱', '핵맛', '맛도리', '존좋', '예쁨', '깔끔',
    '웨이팅', '대기', '예약', '주문', '포장', '배달',
    '내돈내산', '담백', '매우', '영롱한', '든든한',
    '따뜻한', '시원하게', '푸짐한', '푸짐', '부드럽고',
    '놓칠', '뚜껑', '이유', '때마침', '뜻밖', '서린',
    '둘이서', '가게', '카페', '식당', '메뉴', '추천',
    '레시피', '재료', '양파', '파주시', '역삼', '정문',
    '복잡하게', '다이어트', '오븐',
    '총정리', '아이스', '맞집', '스튜디오', '메뉴별',
    '듬뿍담뿍', '초동', '가나안', '외식',
    '하이킹', '운동', '격렬한', '조용히',
    '헤어', '오목로18번길', '국민내일배움카드', '기술',
    '생생정보', '짜파게티', '안드로이드', 'USB디버깅',
    '아침', '육즙', '주차', '선지', '노형점', '후라이드',
    '갈비맛', 'BHC치킨', '커리퀸', '비주얼', '밀가루옷',
    '많아서', '원노트', '파일', '계정', '텐조', '낙지젓',
    'HMR', '함덕', '대동단결', '테일즈위버', '템세팅', '보물창고',
    '[성시경', '[치킨', '[김해', '[뉴스큐레이션]', '[제주',
    '[스크랩', '[맥도날드', '국민의힘', '이재명', '박은정',
    '참조은생고기', '회차정보', '독신', '찾아준', '허리', '건강한',
    '60계치킨', '매콤하니', '저같', '쪼끔', "'피에솔레'",
    '실비김치', '매운', '시원한', '더현대서울', '파이브가이즈',
    '햄버거', '리틀버거', '비조리', '전국', '&lt;서울',
    '제철', '따뜻한', '야식',  '할리스', '파주시',
    '아프리카TV', '플랫폼', '채팅', '쿠키', '슈퍼채팅',
    '어플리케이션', '70mai', '동인천역', '엄청난',
     '돌판', '은희네소고기해장국',
    '은희네해장국', '두찜🐔', 'www', '5dari', 'co', 'kr',
     '당면잡채','영화', '검색', '50위', '엑셀',
}

# 시간/수량 표현
TIME_QUANTITY_BLACKLIST = {
    '아침', '저녁', '점심', '새벽', '오전', '오후', '밤늦게',
    '두가지', '세가지', '1메뉴', '인원수', '1박',
    '석달간', '31가지', '50여가지메뉴', '2박3일동안',
    '평일', '주말', '일요일', '월요일',
}

# 숫자/특수문자 패턴
NUMBER_SPECIAL_PATTERN = re.compile(r'^[\d\s\.,\-\+\(\)\[\]\{\}\/\\:;!?@#$%^&*_=<>]+$')
LOCATION_NUMBER_PATTERN = re.compile(r'\d+번길|\d+층|\d+m|\d+만원|\d+원')


class NERPostprocessor:
    """NER 후처리기 v4"""
    
    def _contains_special_chars(self, text: str) -> bool:
        """특수문자 포함 여부 (한글/영문/숫자/공백 제외)"""
        # 한글, 영문, 숫자, 공백만 허용
        cleaned = re.sub(r'[\uAC00-\uD7A3a-zA-Z0-9\s]', '', text)
        return len(cleaned) > 0
    
    def _is_valid_menu(self, text: str) -> bool:
        """메뉴로 유효한지 판단 (화이트리스트 검증)"""
        if not text or len(text) < 2:
            return False
        
        # 블랙리스트 체크
        if text in SLANG_BLACKLIST:
            return False
        if text in LOCATION_NAMES:
            return False
        if text in TIME_QUANTITY_BLACKLIST:
            return False
        
        # 숫자/특수문자만 있는 경우
        if NUMBER_SPECIAL_PATTERN.match(text):
            return False
        
        # 특수문자 포함 체크 (아프리카TV/티비 등)
        if self._contains_special_chars(text):
            return False
        
        # 주소/전화번호 패턴
        if LOCATION_NUMBER_PATTERN.search(text):
            return False
        
        # 너무 긴 경우
        if len(text) > 30:
            return False
        
        # ✅ 화이트리스트 검증: 조리법 또는 재료를 포함해야 함
        has_cooking = any(suffix in text for suffix in VALID_COOKING_SUFFIXES)
        has_ingredient = any(ingredient in text for ingredient in VALID_INGREDIENTS)
        
        # 조리법과 재료 중 하나라도 포함해야 메뉴로 인정
        if not has_cooking and not has_ingredient:
            return False
        
        return True
    
    def clean_entity(self, text: str) -> Optional[str]:
        """엔티티 정제"""
        if not text:
            return None
        
        # 특수문자 제거 (앞뒤)
        text = re.sub(r'[,.\s!?~…\[\]\(\)\{\}<>"\'`]+$', '', text)
        text = re.sub(r'^[,.\s!?~…\[\]\(\)\{\}<>"\'`]+', '', text)
        text = text.strip()
        
        # 조사 제거
        postpositions = ['에서', '에게', '한테', '께', '를', '을', '이', '가', '의',
                        '에', '으로', '로', '와', '과', '랑', '하고', '도', '만',
                        '까지', '부터', '조차', '마저', '나마', '은', '는']
        sorted_postpositions = sorted(postpositions, key=len, reverse=True)
        for post in sorted_postpositions:
            if text.endswith(post) and len(text) > len(post):
                candidate = text[:-len(post)]
                if not self._is_protected_suffix(text):
                    text = candidate
        
        text = text.strip()
        
        if not text or len(text) < 2:
            return None
        
        return text
    
    def _is_protected_suffix(self, text: str) -> bool:
        """조리법/명사 보호"""
        protected = ['구이', '회', '찌개', '전골', '무침', '나물', '조림', '볶음',
                    '찜', '튀김', '전', '적', '말이', '꼬치', '탕', '국', '밥',
                    '면', '국수', '냉이', '쑥', '미나리', '부추', '시래기', '곤드레',
                    '고사리', '취나물', '참나물', '달래', '두릅']
        for suffix in protected:
            if text.endswith(suffix):
                return True
        return False
    
    def process(self, entities: List[str], entity_type: str = 'menu') -> List[str]:
        """전체 후처리 파이프라인"""
        if not entities:
            return []
        
        # 1. 각 엔티티 정제
        cleaned = [self.clean_entity(e) for e in entities]
        
        # 2. None 제거 + 유효성 검증 (화이트리스트)
        cleaned = [e for e in cleaned if e is not None and self._is_valid_menu(e)]
        
        # 3. 중복 제거 (순서 유지)
        seen = set()
        unique = []
        for e in cleaned:
            if e not in seen:
                seen.add(e)
                unique.append(e)
        
        return unique


# ─────────────── 단독 실행 (테스트) ───────────────
if __name__ == '__main__':
    processor = NERPostprocessor()
    
    print("=" * 60)
    print("🧪 NER 후처리기 v4 테스트 (화이트리스트 검증)")
    print("=" * 60)
    
    test_cases = [
        # 오추출 사례 (모두 제거되어야 함)
        (['아프리카TV/티비', '플랫폼', '채팅', '쿠키', '슈퍼채팅'], 'menu'),
        (['어플리케이션'], 'menu'),
        (['서린', '내돈내산', '담백'], 'menu'),
        (['수원', '두가지', '선지'], 'menu'),
        (['매우', '둘이서'], 'menu'),
        (['석달간', '아침', '저녁', '치밥하기', '냉동품'], 'menu'),
        (['갈치조림', '가게'], 'menu'),
        (['은희네소고기해장국', '소고기해장국', '은희네해장국', '카페'], 'menu'),
        (['몸국', '2층'], 'menu'),
        # 정상 사례 (유지되어야 함)
        (['고기국수'], 'menu'),
        (['김치찌개'], 'menu'),
        (['흑돼지구이'], 'menu'),
        (['소고기해장국'], 'menu'),
        (['된장찌개'], 'menu'),
        (['물회'], 'menu'),
        (['전복죽'], 'menu'),
    ]
    
    for entities, entity_type in test_cases:
        result = processor.process(entities, entity_type)
        print(f"   {entities} → {result}")
    
    print("\n" + "=" * 60)
    print("📋 기대 결과:")
    print("   오추출 사례 → 전부 [] (빈 배열)")
    print("   정상 사례 → 유지")