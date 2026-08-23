"""
도메인 모델: CertRecord (Pydantic v2 기반)
==========================================
- Bronze/Silver/Gold 전 계층에서 사용하는 단일 진실 공급원(SSOT)
- 런타임 검증으로 데이터 무결성 보장
- 기존 adapters/base.py의 CertRecord dataclass를 Pydantic으로 전환

기존 참고:
- adapters/base.py: CertRecord dataclass 정의
- pipeline.py: 필드 사용 방식
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Optional, Any

from pydantic import BaseModel, Field, field_validator, ConfigDict


class CertRecord(BaseModel):
    """
    공공데이터 인증 식당 레코드 (SSOT)

    이 모델은 파이프라인 전 계층에서 사용하는 단일 데이터 구조입니다.
    Bronze(수집) → Silver(정제) → Gold(적재) 과정에서 이 모델이 변환됩니다.
    """

    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
        frozen=False,
        populate_by_name=True,
    )

    # ─── 식별자 ───
    source: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description='어댑터 식별자 (e.g., safe_restaurant, food_safety)'
    )
    cert_type: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description='인증 타입 (e.g., food_safety, low_sodium)'
    )
    dedup_key: Optional[str] = Field(
        default=None,
        max_length=64,
        description='sha256 해시 기반 중복 제거 키 (Silver Layer에서 생성)'
    )

    # ─── 기본 정보 ───
    raw_name: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description='상호명 (원본)'
    )
    raw_address: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description='주소 (원본)'
    )

    # ─── 주소 3계층 ───
    province: Optional[str] = Field(
        default=None,
        max_length=50,
        description='시/도 (e.g., 서울특별시)'
    )
    municipality: Optional[str] = Field(
        default=None,
        max_length=50,
        description='시/군/구 (e.g., 강남구)'
    )
    emd: Optional[str] = Field(
        default=None,
        max_length=50,
        description='읍/면/동 (e.g., 역삼동)'
    )
    road_name: Optional[str] = Field(
        default=None,
        max_length=300,
        description='도로명 주소'
    )
    lot_num: Optional[str] = Field(
        default=None,
        max_length=300,
        description='지번 주소'
    )

    # ─── 연락처/업종 ───
    phone: Optional[str] = Field(
        default=None,
        max_length=20,
        description='전화번호 (정규화됨)'
    )
    biz_type: Optional[str] = Field(
        default=None,
        max_length=100,
        description='업종 (e.g., 한식, 중식)'
    )

    # ─── 메뉴 ───
    main_menu: Optional[str] = Field(
        default=None,
        max_length=100,
        description='대표 메뉴'
    )
    main_menu_source: Optional[str] = Field(
        default=None,
        pattern=r'^(public_api|cross_source|naver_api|kakao_api|naver_blog|naver_cafe|naver_news|llm_verified|llm_filled|original)$',
        description='대표 메뉴 출처'
    )
    menus: list[str] = Field(
        default_factory=list,
        max_length=20,
        description='추가 메뉴 목록'
    )
    menus_source: Optional[str] = Field(
        default=None,
        description='추가 메뉴 출처'
    )

    # ─── 인증 ───
    certs: list[str] = Field(
        default_factory=list,
        description='인증 타입 목록 (e.g., [food_safety, low_sodium])'
    )
    designated_at: Optional[date] = Field(
        default=None,
        description='지정일자'
    )
    cancelled_at: Optional[date] = Field(
        default=None,
        description='지정취소일자'
    )
    closed_at: Optional[date] = Field(
        default=None,
        description='폐업일자'
    )
    is_active: bool = Field(
        default=True,
        description='활성 상태 여부'
    )

    # ─── 좌표 ───
    lat: Optional[float] = Field(
        default=None,
        ge=33.0,
        le=38.6,
        description='위도 (대한민국 범위)'
    )
    lng: Optional[float] = Field(
        default=None,
        ge=124.5,
        le=132.0,
        description='경도 (대한민국 범위)'
    )
    coord_confidence: Optional[str] = Field(
        default=None,
        pattern=r'^(high|medium|low)$',
        description='좌표 신뢰도'
    )

    # ─── 7차원 태그 ───
    cuisine_tags: list[str] = Field(
        default_factory=list,
        description='요리 타입 태그'
    )
    nutrient_tags: list[str] = Field(
        default_factory=list,
        description='영양소 태그'
    )
    cooking_tags: list[str] = Field(
        default_factory=list,
        description='조리법 태그'
    )
    ingredient_tags: list[str] = Field(
        default_factory=list,
        description='재료 태그'
    )
    regional_tags: list[str] = Field(
        default_factory=list,
        description='지역 특색 태그'
    )
    allergen_tags: list[str] = Field(
        default_factory=list,
        description='알레르기 유발 태그'
    )
    occasion_tags: list[str] = Field(
        default_factory=list,
        description='상황/분위기 태그'
    )

    # ─── avoid_tags (자동 생성) ───
    avoid_tags: list[str] = Field(
        default_factory=list,
        description='회피 태그 (allergen_tags 기반 자동 생성)'
    )

    # ─── 품질/메타 ───
    
    # 🆕 위생등급 필드 추가
    hygiene_grade: Optional[str] = Field(
        default=None,
        pattern=r'^(갑|을|병)$',
        description='위생등급 (갑/을/병)'
    )
    
    # 데이터 품질 필드 추가
    store_name_quality: Optional[str] = Field(
        default='valid',
        description='가게 이름 품질 (valid/needs_verify)',
    )
    
    # 데이터 품질 필드 추가
    store_name_quality: Optional[str] = Field(
        default='valid',
        description='가게 이름 품질 (valid/needs_verify)',
    )
    
    quality_score: int = Field(
        default=0,
        ge=0,
        le=100,
        description='데이터 품질 점수 (0~100)'
    )
    ghost_suspicion_score: int = Field(
        default=0,
        ge=0,
        le=100,
        description='유령가게 의심 점수 (0~100)'
    )
    data_source: Optional[str] = Field(
        default=None,
        description='데이터 출처 식별자'
    )

    # ─── 원본 데이터 (Bronze 보존용 — 직렬화에서 제외) ───
    raw: Optional[dict[str, Any]] = Field(
        default=None,
        exclude=True,
        description='원본 API 응답 데이터 (Bronze Layer 보존용)'
    )

    # ─── 검증자 ───
    @field_validator(
        'menus', 'cuisine_tags', 'nutrient_tags', 'cooking_tags',
        'ingredient_tags', 'regional_tags', 'allergen_tags',
        'occasion_tags', 'avoid_tags', 'certs',
        mode='before'
    )
    @classmethod
    def ensure_list(cls, v: Any) -> list[str]:
        """None이나 문자열이 들어와도 리스트로 변환"""
        if v is None:
            return []
        if isinstance(v, str):
            if not v:
                return []
            return [v]
        if isinstance(v, list):
            return [str(item) for item in v if item]
        return []

    @field_validator('phone', mode='before')
    @classmethod
    def normalize_phone(cls, v: Any) -> Optional[str]:
        """
        전화번호 정규화

        규칙:
        - 숫자만 추출
        - 0000으로 시작하면 None (가짜 번호)
        - 9자리 미만이면 None (불완전 번호)
        - 기존 clean_phone() 로직 보존
        """
        if not v:
            return None

        # 기존 pipeline.py의 clean_phone() 로직
        v_str = str(v)
        if '****' in v_str or v_str.startswith('000-'):
            return None

        cleaned = re.sub(r'\D', '', v_str)
        if not cleaned or len(cleaned) < 9:
            return None

        return cleaned

    @field_validator('designated_at', 'cancelled_at', 'closed_at', mode='before')
    @classmethod
    def parse_date(cls, v: Any) -> Optional[date]:
        """
        날짜 파싱

        지원 형식:
        - YYYY-MM-DD
        - YYYYMMDD
        - date/datetime 객체
        """
        if not v:
            return None
        if isinstance(v, date):
            return v
        if isinstance(v, datetime):
            return v.date()

        s = str(v).strip()
        if not s:
            return None

        # YYYY-MM-DD 형식
        match = re.match(r'^(\d{4})-(\d{2})-(\d{2})$', s)
        if match:
            try:
                return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            except ValueError:
                return None

        # YYYYMMDD 형식
        match = re.match(r'^(\d{4})(\d{2})(\d{2})$', s)
        if match:
            try:
                return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            except ValueError:
                return None

        return None

    @field_validator('main_menu_source', mode='before')
    @classmethod
    def normalize_menu_source(cls, v: Any) -> Optional[str]:
        """메뉴 출처 정규화"""
        if not v:
            return None
        v_str = str(v).strip().lower()

        # 기존 pipeline.py에서 사용하는 source 값 매핑
        valid_sources = {
            'public_api', 'cross_source', 'naver_api', 'kakao_api',
            'naver_blog', 'naver_cafe', 'naver_news',
            'llm_verified', 'llm_filled', 'original',
        }
        if v_str in valid_sources:
            return v_str

        # 알 수 없는 source는 None으로 처리
        return None

    # ─── 메서드 ───
    def is_valid(self) -> bool:
        """
        활성 상태 검증

        기존 adapters/base.py의 is_valid() 메서드 보존
        """
        return (
            self.is_active
            and self.cancelled_at is None
            and self.closed_at is None
            and bool(self.raw_name)
            and bool(self.raw_address)
        )

    def has_coordinate(self) -> bool:
        """좌표 유효성 검증"""
        return (
            self.lat is not None
            and self.lng is not None
            and 33.0 <= self.lat <= 38.6
            and 124.5 <= self.lng <= 132.0
        )

    def get_full_address(self) -> str:
        """전체 주소 조합"""
        parts = [
            self.province or '',
            self.municipality or '',
            self.emd or '',
            self.road_name or self.lot_num or '',
        ]
        return ' '.join(p for p in parts if p).strip()