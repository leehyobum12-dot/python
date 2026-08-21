"""
Gold Layer 단위 테스트
=======================
- 변환
- 내보내기
"""
from __future__ import annotations

import json
import os
import tempfile
import pytest

from bareun_pipeline.gold.transform import GoldTransformer, GoldRestaurant
from bareun_pipeline.gold.export import GoldExporter


class TestGoldTransformer:
    """변환 테스트"""

    def test_transform_record(self, sample_cert_record):
        """레코드 변환"""
        transformer = GoldTransformer()
        gold_records = transformer.transform([sample_cert_record])

        assert len(gold_records) == 1
        assert gold_records[0].store_name == '올래국수'
        assert gold_records[0].province == '제주특별자치도'

    def test_quality_score(self, sample_cert_record):
        """품질 점수 계산"""
        transformer = GoldTransformer()
        gold_records = transformer.transform([sample_cert_record])

        assert 0 <= gold_records[0].quality_score <= 100
        assert gold_records[0].quality_score > 50  # 기본 정보가 있으므로 높은 점수

    def test_to_payload(self, sample_gold_restaurant):
        """payload 변환"""
        payload = sample_gold_restaurant.to_payload()

        assert payload['store_name'] == '올래국수'
        assert payload['province'] == '제주특별자치도'
        assert isinstance(payload['menus'], list)
        assert isinstance(payload['cuisine_tags'], list)


class TestGoldExporter:
    """내보내기 테스트"""

    def test_export_ndjson(self, sample_gold_restaurant):
        """NDJSON 내보내기"""
        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = GoldExporter(gold_dir=tmpdir)

            filepath = exporter.export_ndjson([sample_gold_restaurant])

            assert os.path.exists(filepath)
            with open(filepath, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            assert len(lines) == 1

            data = json.loads(lines[0])
            assert data['store_name'] == '올래국수'

    def test_export_csv(self, sample_gold_restaurant):
        """CSV 내보내기"""
        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = GoldExporter(gold_dir=tmpdir)

            filepath = exporter.export_csv([sample_gold_restaurant])

            assert os.path.exists(filepath)
            assert filepath.endswith('.csv')

    def test_export_dual(self, sample_gold_restaurant):
        """이중 출력 (NDJSON + CSV)"""
        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = GoldExporter(gold_dir=tmpdir)

            result = exporter.export_dual([sample_gold_restaurant])

            assert result['count'] == 1
            assert os.path.exists(result['ndjson_path'])
            assert os.path.exists(result['csv_path'])