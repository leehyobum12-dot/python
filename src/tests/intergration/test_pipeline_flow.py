"""
Bronze → Silver → Gold 파이프라인 통합 테스트
=============================================
외부 API 호출을 Mock하여 전체 파이프라인 데이터 흐름을 검증합니다.
"""
from __future__ import annotations

from unittest.mock import Mock, patch
from datetime import date

import pytest

from bareun_pipeline.domain.record import CertRecord
from bareun_pipeline.domain.avoid_tags import generate_avoid_tags
from bareun_pipeline.silver.normalize import Normalizer, normalize_records
from bareun_pipeline.silver.merge import Merger, merge_records
from bareun_pipeline.silver.classify import TaxonomyClassifier
from bareun_pipeline.gold.transform import GoldTransformer
from bareun_pipeline.gold.export import GoldExporter


class TestPipelineIntegration:
    """Bronze → Silver → Gold 파이프라인 통합 테스트"""

    def test_full_pipeline_flow(self, sample_cert_records):
        """
        전체 파이프라인 흐름 테스트:
        1. Bronze: 원본 데이터 수집 (Mock)
        2. Silver: 정규화 + 병합 + 분류
        3. Gold: 변환 + 내보내기
        """
        # Step 1: Bronze (Mock - 이미 sample_cert_records로 대체)
        bronze_records = sample_cert_records
        assert len(bronze_records) == 3

        # Step 2: Silver - 정규화
        normalized = normalize_records(bronze_records)
        assert len(normalized) == 3

        # Step 3: Silver - 병합
        merged = merge_records(normalized)
        # 올래국수 2개 병합 + 우진해장국 1개 = 2개
        assert len(merged) == 2

        # Step 4: Silver - 분류 (avoid_tags 별도 생성)
        classifier = TaxonomyClassifier()
        for record in merged:
            text = record.main_menu or record.raw_name
            tags = classifier.classify(text, record.biz_type or '')
            record.cuisine_tags = tags['cuisine_tags']
            record.nutrient_tags = tags['nutrient_tags']
            record.cooking_tags = tags['cooking_tags']
            record.ingredient_tags = tags['ingredient_tags']
            record.regional_tags = tags['regional_tags']
            record.allergen_tags = tags['allergen_tags']
            record.occasion_tags = tags['occasion_tags']
            # avoid_tags는 allergen_tags 기반으로 별도 생성
            record.avoid_tags = generate_avoid_tags(tags['allergen_tags'])

        # Step 5: Gold - 변환
        transformer = GoldTransformer()
        gold_records = transformer.transform(merged)
        assert len(gold_records) == 2

        # Step 6: Gold - 내보내기 (임시 디렉토리)
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = GoldExporter(gold_dir=tmpdir)
            result = exporter.export_dual(gold_records)

            assert result['count'] == 2
            assert result['ndjson_path'].endswith('.ndjson')
            assert result['csv_path'].endswith('.csv')

    def test_pipeline_with_invalid_records(self):
        """유효하지 않은 레코드가 포함된 파이프라인 테스트"""
        records = [
            # 유효한 레코드
            CertRecord(
                source='test',
                cert_type='test',
                raw_name='올래국수',
                raw_address='제주특별자치도 제주시 중앙로 1길 10',
                province='제주특별자치도',
                municipality='제주시',
                phone='0641234567',
                is_active=True,
            ),
            # 유효하지 않은 레코드 (취소됨)
            CertRecord(
                source='test',
                cert_type='test',
                raw_name='폐업한식당',
                raw_address='제주특별자치도 제주시',
                cancelled_at=date(2024, 1, 1),
                is_active=False,
            ),
        ]

        # 병합 시 유효하지 않은 레코드 제외
        merged = merge_records(records)
        assert len(merged) == 1
        assert merged[0].raw_name == '올래국수'

    def test_pipeline_dedup_key_consistency(self, sample_cert_records):
        """dedup_key 일관성 테스트"""
        # 병합 전 dedup_key 생성
        normalized = normalize_records(sample_cert_records)
        for record in normalized:
            if not record.dedup_key:
                from bareun_pipeline.domain.dedup import generate_dedup_key
                record.dedup_key = generate_dedup_key(
                    record.raw_name,
                    record.raw_address,
                    record.phone,
                )

        # 병합
        merged = merge_records(normalized)

        # 모든 레코드가 dedup_key를 가져야 함
        for record in merged:
            assert record.dedup_key is not None
            assert len(record.dedup_key) == 32

    def test_pipeline_avoid_tags_generation(self):
        """avoid_tags 자동 생성 테스트"""
        records = [
            CertRecord(
                source='test',
                cert_type='test',
                raw_name='새우튀김전문점',
                raw_address='제주특별자치도 제주시',
                main_menu='새우튀김',
                is_active=True,
            ),
        ]

        # 분류 후 avoid_tags 별도 생성
        classifier = TaxonomyClassifier()
        for record in records:
            tags = classifier.classify(record.main_menu, record.biz_type or '')
            record.allergen_tags = tags['allergen_tags']
            # avoid_tags는 allergen_tags 기반으로 별도 생성
            record.avoid_tags = generate_avoid_tags(tags['allergen_tags'])

        # 새우 → 갑각류·조개류 → 갑각류, 조개류
        assert '갑각류·조개류' in records[0].allergen_tags
        assert '갑각류' in records[0].avoid_tags
        assert '조개류' in records[0].avoid_tags

    def test_pipeline_with_classify_and_add_avoid_tags(self):
        """classify_and_add_avoid_tags() 통합 테스트"""
        classifier = TaxonomyClassifier()
        tags = classifier.classify_and_add_avoid_tags('새우튀김', '한식')

        # 7차원 태그 + avoid_tags 모두 포함
        assert 'cuisine_tags' in tags
        assert 'allergen_tags' in tags
        assert 'avoid_tags' in tags
        assert '갑각류·조개류' in tags['allergen_tags']
        assert '갑각류' in tags['avoid_tags']


class TestPipelineWithMockedAPI:
    """Mock된 외부 API를 사용한 파이프라인 테스트"""

    @patch('bareun_pipeline.adapters.safe_restaurant.SafeRestaurantAdapter.collect')
    def test_bronze_with_mocked_api(self, mock_collect):
        """Mock된 어댑터를 사용한 Bronze Layer 테스트"""
        # Mock 데이터 설정
        mock_collect.return_value = [
            CertRecord(
                source='safe_restaurant',
                cert_type='safe_restaurant',
                raw_name='테스트식당',
                raw_address='제주특별자치도 제주시 테스트로 1',
                province='제주특별자치도',
                municipality='제주시',
                is_active=True,
            ),
        ]

        # 어댑터 호출
        from bareun_pipeline.adapters.safe_restaurant import SafeRestaurantAdapter
        adapter = SafeRestaurantAdapter()
        records = adapter.collect(province='제주특별자치도')

        # 검증
        assert len(records) == 1
        assert records[0].raw_name == '테스트식당'
        mock_collect.assert_called_once_with(province='제주특별자치도')