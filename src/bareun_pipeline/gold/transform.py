"""
Gold Layer: DB 스키마 매칭
============================
- Silver Layer의 CertRecord를 Supabase restaurants 테이블 스키마로 변환
- Pydantic 모델로 출력 검증
- quality_score 계산

기존 참고:
- pipeline.py의 CSV 출력 컬럼
- ingest_to_supabase.py의 payload 변환
- Supabase SQL v4.8의 restaurants 테이블 스키마

보존 사항:
- quality_score 계산 로직
- 메뉴 출처 우선순위
- 태그 배열 정렬

개선 사항:
- GoldRestaurant Pydantic 모델로 검증
- reject 로깅
- 타입 안전성 강화
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..domain.dedup import generate_dedup_key
from ..domain.record import CertRecord
from ..observability.logging import get_logger
from ..settings import get_settings

logger = get_logger(__name__)


class GoldRestaurant(BaseModel):
    """
    Supabase bulk_upsert_restaurants payload 스키마

    이 모델은 Supabase restaurants 테이블의 컬럼과 정확히 매칭됩니다.
    """

    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
        frozen=False,
    )

    # 식별자
    dedup_key: str = Field(
        ..., min_length=1, max_length=64, description='sha256 해시 기반 중복 제거 키'
    )
    data_source: str = Field(
        ..., max_length=50, description='public_api|cross_source|naver_api|kakao_api'
    )

    # 기본 정보
    store_name: str = Field(..., min_length=2, max_length=100)
    province: str = Field(..., max_length=50)
    municipality: str = Field(..., max_length=50)
    emd: str | None = Field(default=None, max_length=50)

    # 주소
    address_road: str | None = Field(default=None, max_length=200)
    address_lotnum: str | None = Field(default=None, max_length=200)

    # 좌표
    lat: float | None = Field(None, ge=33.0, le=38.6)
    lng: float | None = Field(None, ge=124.5, le=132.0)

    # 메뉴
    main_menu: str | None = Field(default=None, max_length=50)
    main_menu_source: str | None = Field(default=None)
    menus: list[str] = Field(default_factory=list, max_length=10)
    menus_source: str | None = Field(default=None)

    # 연락처/업종
    phone: str | None = Field(default=None, max_length=20)
    biz_type: str | None = Field(default=None, max_length=100)

    # 인증
    certs: list[str] = Field(default_factory=list)
    designated_at: date | None = Field(default=None)

    # 7차원 태그
    cuisine_tags: list[str] = Field(default_factory=list)
    nutrient_tags: list[str] = Field(default_factory=list)
    cooking_tags: list[str] = Field(default_factory=list)
    ingredient_tags: list[str] = Field(default_factory=list)
    regional_tags: list[str] = Field(default_factory=list)
    allergen_tags: list[str] = Field(default_factory=list)
    occasion_tags: list[str] = Field(default_factory=list)

    # avoid_tags (자동 생성)
    avoid_tags: list[str] = Field(default_factory=list)

    # 품질
    quality_score: int = Field(default=0, ge=0, le=100)

    @field_validator(
        'menus',
        'cuisine_tags',
        'nutrient_tags',
        'cooking_tags',
        'ingredient_tags',
        'regional_tags',
        'allergen_tags',
        'occasion_tags',
        'avoid_tags',
        'certs',
        mode='before',
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

    def to_payload(self) -> dict[str, Any]:
        """bulk_upsert_restaurants RPC용 payload 변환"""
        return {
            'store_name': self.store_name,
            'province': self.province,
            'municipality': self.municipality,
            'emd': self.emd,
            'address_road': self.address_road,
            'address_lotnum': self.address_lotnum,
            'main_menu': self.main_menu,
            'main_menu_source': self.main_menu_source,
            'menus': self.menus,
            'menus_source': self.menus_source,
            'quality_score': self.quality_score,
            'lat': self.lat,
            'lng': self.lng,
            'phone': self.phone,
            'biz_type': self.biz_type,
            'certs': self.certs,
            'data_source': self.data_source,
            'designated_at': self.designated_at.isoformat() if self.designated_at else None,
            'dedup_key': self.dedup_key,
            'cuisine_tags': self.cuisine_tags,
            'nutrient_tags': self.nutrient_tags,
            'cooking_tags': self.cooking_tags,
            'ingredient_tags': self.ingredient_tags,
            'regional_tags': self.regional_tags,
            'allergen_tags': self.allergen_tags,
            'occasion_tags': self.occasion_tags,
            'avoid_tags': self.avoid_tags,
        }


class GoldTransformer:
    """
    Silver → Gold 변환기

    사용법:
        transformer = GoldTransformer()
        gold_records = transformer.transform(silver_records)
    """

    # 메뉴 출처 점수
    MENU_SOURCE_SCORE: dict[str, int] = {
        'public_api': 10,
        'cross_source': 8,
        'naver_api': 6,
        'kakao_api': 6,
        'naver_blog': 5,
        'naver_cafe': 5,
        'naver_news': 4,
        'llm_verified': 7,
        'llm_filled': 6,
        'original': 5,
    }

    def transform(self, records: list[CertRecord]) -> list[GoldRestaurant]:
        """
        CertRecord 목록을 GoldRestaurant으로 변환

        Args:
            records: 변환할 레코드 목록

        Returns:
            변환된 GoldRestaurant 목록
        """
        logger.info('gold_transform_start', count=len(records))

        results: list[GoldRestaurant] = []
        rejects: list[dict[str, Any]] = []

        for record in records:
            try:
                gold = self._transform_one(record)
                results.append(gold)
            except Exception as e:
                rejects.append(
                    {
                        'record': record.model_dump()
                        if hasattr(record, 'model_dump')
                        else str(record),
                        'error': str(e),
                        'error_type': type(e).__name__,
                    }
                )
                logger.warning(
                    'gold_transform_reject',
                    name=record.raw_name,
                    error=str(e),
                )

        if rejects:
            logger.info('gold_transform_rejects', count=len(rejects))
            self._save_rejects(rejects)

        logger.info(
            'gold_transform_complete',
            success=len(results),
            rejects=len(rejects),
        )

        return results

    def _transform_one(self, record: CertRecord) -> GoldRestaurant:
        """단일 레코드 변환"""
        # dedup_key 생성 (아직 없는 경우)
        dedup_key = record.dedup_key
        if not dedup_key:
            dedup_key = generate_dedup_key(
                raw_name=record.raw_name,
                raw_address=record.raw_address,
                phone=record.phone,
                detail_address=record.lot_num or record.road_name,
            )

        if not dedup_key:
            raise ValueError('dedup_key 생성 실패')

        # data_source 결정
        data_source = record.data_source or record.source or 'public_api'

        # quality_score 계산
        quality_score = self._calculate_quality_score(record)

        return GoldRestaurant(
            dedup_key=dedup_key,
            data_source=data_source,
            store_name=record.raw_name,
            province=record.province or '',
            municipality=record.municipality or '',
            emd=record.emd,
            address_road=record.road_name,
            address_lotnum=record.lot_num,
            lat=record.lat,
            lng=record.lng,
            main_menu=record.main_menu,
            main_menu_source=record.main_menu_source,
            menus=record.menus,
            menus_source=record.menus_source,
            phone=record.phone,
            biz_type=record.biz_type,
            certs=record.certs,
            designated_at=record.designated_at,
            cuisine_tags=record.cuisine_tags,
            nutrient_tags=record.nutrient_tags,
            cooking_tags=record.cooking_tags,
            ingredient_tags=record.ingredient_tags,
            regional_tags=record.regional_tags,
            allergen_tags=record.allergen_tags,
            occasion_tags=record.occasion_tags,
            avoid_tags=record.avoid_tags,
            quality_score=quality_score,
        )

    def _calculate_quality_score(self, record: CertRecord) -> int:
        """
        레코드별 데이터 품질 점수 계산

        기존 pipeline.py의 calculate_quality_score() 함수 보존

        Args:
            record: 점수 계산할 레코드

        Returns:
            품질 점수 (0~100)
        """
        score = 0

        # 기본 정보
        if record.raw_name:
            score += 10
        if record.raw_address:
            score += 10
        if record.phone:
            score += 10
        if record.main_menu:
            score += 10

        # 주소 3계층
        if record.province:
            score += 5
        if record.municipality:
            score += 5
        if record.emd:
            score += 5
        if record.road_name:
            score += 5
        if record.lot_num:
            score += 5

        # 좌표
        if record.has_coordinate():
            score += 15

        # 메뉴 출처 점수
        score += self.MENU_SOURCE_SCORE.get(record.main_menu_source or '', 0)

        # 인증 개수
        if record.certs:
            cert_count = len(record.certs)
            score += min(cert_count * 3, 10)

        return min(score, 100)

    def _save_rejects(self, rejects: list[dict[str, Any]]) -> None:
        """검증 실패 데이터 저장"""
        settings = get_settings()
        os.makedirs(settings.rejects_dir, exist_ok=True)

        filename = f'gold_rejects_{datetime.now().strftime("%Y%m%d_%H%M%S")}.jsonl'
        filepath = os.path.join(settings.rejects_dir, filename)

        with open(filepath, 'w', encoding='utf-8') as f:
            for reject in rejects:
                f.write(json.dumps(reject, ensure_ascii=False, default=str) + '\n')

        logger.info('gold_rejects_saved', path=filepath, count=len(rejects))


def transform_records(records: list[CertRecord]) -> list[GoldRestaurant]:
    """레코드 변환 (편의 함수)"""
    transformer = GoldTransformer()
    return transformer.transform(records)
