"""
환경변수 중앙 관리 (Pydantic Settings)
======================================
- .env 파일에서 로드
- 런타임 검증 (필수값, 타입, 형식)
- SSOT: 모든 설정은 이 파일에서만 관리

기존 참고 파일:
- config/paths.py (경로 상수)
- config/thresholds.py (임계값)
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field, field_validator, ConfigDict
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """바른인증식당 파이프라인 설정 (SSOT)"""

    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        case_sensitive=False,
        extra='ignore',
    )

    # ─── Supabase ───
    supabase_url: str = Field(
        ...,
        description='Supabase 프로젝트 URL (https://*.supabase.co)'
    )
    supabase_service_role_key: str = Field(
        ...,
        description='Supabase Service Role Key (RLS 우회 — 로컬 파이프라인 전용)'
    )
    supabase_anon_key: Optional[str] = Field(
        default=None,
        description='Supabase Anon Key (프론트엔드용 — 선택)'
    )

    # ─── 공공데이터 API 키 ───
    mafra_api_key: Optional[str] = Field(
        default=None,
        description='농식품부 안심식당 API 키'
    )
    data_go_kr_key: Optional[str] = Field(
        default=None,
        description='공공데이터포털 API 키'
    )
    foodsafety_korea_key: Optional[str] = Field(
        default=None,
        description='식품안전나라 API 키'
    )

    # ─── 네이버 API ───
    naver_client_id: Optional[str] = Field(
        default=None,
        description='네이버 클라우드 플랫폼 Client ID (지오코딩)'
    )
    naver_client_secret: Optional[str] = Field(
        default=None,
        description='네이버 클라우드 플랫폼 Client Secret (지오코딩)'
    )
    naver_search_id: Optional[str] = Field(
        default=None,
        description='네이버 API HUB Search ID (블로그/카페/뉴스)'
    )
    naver_search_secret: Optional[str] = Field(
        default=None,
        description='네이버 API HUB Search Secret'
    )

    # ─── 카카오 API ───
    kakao_rest_api_key: Optional[str] = Field(
        default=None,
        description='카카오 REST API 키 (로컬 검색)'
    )

    # ─── 보안 ───
    dedup_salt: str = Field(
        default='bareun_default_salt_change_me',
        description='dedup_key 생성용 salt (프로덕션에서는 반드시 랜덤 값으로 변경)'
    )

    # ─── 경로 ───
    data_dir: str = Field(
        default_factory=lambda: os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'data'
        ),
        description='데이터 저장 루트 디렉토리'
    )

    # ─── HTTP 설정 ───
    http_timeout_connect: int = Field(
        default=5,
        ge=1,
        le=30,
        description='HTTP 연결 타임아웃 (초)'
    )
    http_timeout_read: int = Field(
        default=15,
        ge=5,
        le=60,
        description='HTTP 읽기 타임아웃 (초)'
    )
    http_max_retries: int = Field(
        default=3,
        ge=0,
        le=10,
        description='HTTP 최대 재시도 횟수'
    )

    # ─── 배치 설정 ───
    checkpoint_max_age_hours: int = Field(
        default=24,
        ge=1,
        le=168,
        description='체크포인트 최대 유효 시간 (시간)'
    )
    bulk_upsert_chunk_size: int = Field(
        default=500,
        ge=100,
        le=5000,
        description='bulk_upsert 청크 크기'
    )

    # ─── 로깅 ───
    log_level: str = Field(
        default='INFO',
        description='로그 레벨 (DEBUG/INFO/WARNING/ERROR)'
    )
    log_json: bool = Field(
        default=True,
        description='JSON 포맷 로그 출력 여부'
    )

    # ─── Ollama (로컬 LLM) ───
    # 기존 config/paths.py에서 이관
    ollama_api_url: str = Field(
        default='http://localhost:11434/api/generate',
        description='Ollama API URL'
    )
    ollama_tags_url: str = Field(
        default='http://localhost:11434/api/tags',
        description='Ollama Tags URL'
    )
    ollama_model: str = Field(
        default='qwen2.5:7b',
        description='Ollama 모델명'
    )

    # ─── NER 모델 경로 ───
    # 기존 config/paths.py에서 이관
    ner_model_dir: str = Field(
        default_factory=lambda: os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'data', 'models', 'ner_model_v3_klue'
        ),
        description='NER 모델 저장 경로'
    )

    # ─── NLP 임계값 ───
    # 기존 config/thresholds.py에서 이관
    llm_verify_threshold: float = Field(
        default=0.75,
        ge=0.0,
        le=1.0,
        description='LLM 검증 임계값 (이 미만이면 LLM 호출)'
    )
    store_similarity_threshold: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description='가게명 유사도 임계값 (이 이상이면 가게명으로 판정)'
    )
    menu_min_length: int = Field(
        default=2,
        ge=1,
        le=10,
        description='메뉴 최소 길이'
    )
    menu_max_length: int = Field(
        default=20,
        ge=5,
        le=50,
        description='메뉴 최대 길이'
    )
    max_sub_menus: int = Field(
        default=5,
        ge=1,
        le=20,
        description='서브메뉴 최대 개수'
    )
    llm_text_max_length: int = Field(
        default=600,
        ge=100,
        le=2000,
        description='LLM 프롬프트 텍스트 최대 길이'
    )

    # ─── 신뢰도 가중치 ───
    # 기존 config/thresholds.py에서 이관
    confidence_weights: dict = Field(
        default={
            'whitelist_match': 0.30,
            'store_proximity': 0.20,
            'length_valid': 0.10,
            'no_special_char': 0.10,
            'multi_menu': 0.10,
        },
        description='다차원 신뢰도 가중치'
    )

    # ─── 행정구역 목록 ───
    # 기존 pipeline.py의 PROVINCES에서 이관 (전남광주통합특별시 제거)
    provinces: list[str] = Field(
        default=[
            '서울특별시', '부산광역시', '대구광역시', '인천광역시',
            '대전광역시', '울산광역시', '세종특별자치시',
            '경기도', '강원특별자치도',
            '충청북도', '충청남도', '전북특별자치도',
            '전라남도', '광주광역시',
            '경상북도', '경상남도', '제주특별자치도',
        ],
        description='수집 대상 시도 목록'
    )

    # ─── 검증자 ───
    @field_validator('supabase_url')
    @classmethod
    def validate_supabase_url(cls, v: str) -> str:
        """Supabase URL 형식 검증"""
        if not v.startswith('https://'):
            raise ValueError('supabase_url은 https://로 시작해야 합니다.')
        if '.supabase.co' not in v:
            raise ValueError('supabase_url은 *.supabase.co 도메인이어야 합니다.')
        return v.rstrip('/')

    @field_validator('log_level')
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        """로그 레벨 검증"""
        valid_levels = {'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'}
        v_upper = v.upper()
        if v_upper not in valid_levels:
            raise ValueError(f'log_level은 {valid_levels} 중 하나여야 합니다.')
        return v_upper

    @field_validator('provinces')
    @classmethod
    def validate_provinces(cls, v: list[str]) -> list[str]:
        """시도 목록 검증 (빈 목록 방지)"""
        if not v:
            raise ValueError('provinces 목록이 비어 있을 수 없습니다.')
        return v

    # ─── 경로 속성 ───
    @property
    def raw_dir(self) -> str:
        """원본 데이터 저장 디렉토리"""
        return os.path.join(self.data_dir, 'raw')

    @property
    def silver_dir(self) -> str:
        """Silver 레이어 저장 디렉토리"""
        return os.path.join(self.data_dir, 'silver')

    @property
    def gold_dir(self) -> str:
        """Gold 레이어 저장 디렉토리"""
        return os.path.join(self.data_dir, 'gold')

    @property
    def cache_dir(self) -> str:
        """캐시 저장 디렉토리"""
        return os.path.join(self.data_dir, 'cache')

    @property
    def checkpoint_dir(self) -> str:
        """체크포인트 저장 디렉토리"""
        return os.path.join(self.data_dir, 'checkpoints')

    @property
    def rejects_dir(self) -> str:
        """검증 실패 데이터 저장 디렉토리"""
        return os.path.join(self.data_dir, 'rejects')

    @property
    def models_dir(self) -> str:
        """모델 저장 디렉토리"""
        return os.path.join(self.data_dir, 'models')

    def ensure_directories(self) -> None:
        """필요한 디렉토리 생성"""
        for dir_path in [
            self.data_dir,
            self.raw_dir,
            self.silver_dir,
            self.gold_dir,
            self.cache_dir,
            self.checkpoint_dir,
            self.rejects_dir,
            self.models_dir,
        ]:
            os.makedirs(dir_path, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """설정 인스턴스 반환 (캐싱 — 싱글톤)"""
    return Settings()