"""
Silver Layer 단위 테스트
=========================
- 정규화
- 병합
- 분류
"""
from __future__ import annotations

import pytest

from bareun_pipeline.silver.normalize import Normalizer, normalize_records
from bareun_pipeline.silver.merge import Merger, merge_records
from bareun_pipeline.silver.classify import classify_text


class TestNormalizer:
    """정규화 테스트"""

    def test_clean_name(self):
        """상호명 정제"""
        normalizer = Normalizer()
        assert normalizer._clean_name('  올래국수  ') == '올래국수'
        assert normalizer._clean_name('올래  국수') == '올래 국수'

    def test_split_menus(self):
        """메뉴 분할"""
        from bareun_pipeline.domain.record import CertRecord

        record = CertRecord(
            source='test',
            cert_type='test',
            raw_name='테스트',
            raw_address='테스트 주소',
            main_menu='김치찌개,된장찌개,불고기',
        )
        normalizer = Normalizer()
        normalizer._split_menus(record)

        assert record.main_menu == '김치찌개'
        assert '된장찌개' in record.menus
        assert '불고기' in record.menus


class TestMerger:
    """병합 테스트"""

    def test_merge_records(self, sample_cert_records):
        """레코드 병합"""
        merged = merge_records(sample_cert_records)

        # 올래국수 2개 + 우진해장국 1개 = 3개 입력
        # 올래국수 2개는 병합되어 1개로, 우진해장국 1개 유지
        # 결과: 2개
        assert len(merged) == 2

    def test_merge_preserves_certs(self, sample_cert_records):
        """병합 시 인증 타입 통합"""
        merged = merge_records(sample_cert_records)

        # 올래국수는 safe_restaurant + food_safety 인증 보유
        olle = [r for r in merged if r.raw_name == '올래국수'][0]
        assert 'safe_restaurant' in olle.certs
        assert 'food_safety' in olle.certs


class TestClassifier:
    """분류 테스트"""

    def test_classify_korean_food(self):
        """한식 분류"""
        tags = classify_text('김치찌개', '한식')
        assert '한식' in tags['cuisine_tags']

    def test_classify_seafood(self):
        """수산물 분류"""
        tags = classify_text('갈치조림')
        assert '수산물' in tags['ingredient_tags']

    def test_classify_jeju_regional(self):
        """제주 지역 분류"""
        tags = classify_text('흑돼지구이')
        assert '제주' in tags['regional_tags']

    def test_classify_allergen(self):
        """알레르기 분류"""
        tags = classify_text('새우튀김')
        assert '갑각류·조개류' in tags['allergen_tags']

    def test_classify_empty_text(self):
        """빈 텍스트"""
        tags = classify_text('')
        assert tags['cuisine_tags'] == []
        assert tags['nutrient_tags'] == []