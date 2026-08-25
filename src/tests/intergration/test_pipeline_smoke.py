"""
파이프라인 스모크 테스트 (실제 API 호출 없음)
=============================================
- 전체 흐름 검증 (Mock 데이터 사용)
"""

import pytest


class TestPipelineSmoke:
    """파이프라인 전체 흐름 스모크 테스트"""

    def test_bronze_collector_init(self):
        """Bronze 수집기 초기화"""
        from bareun_pipeline.bronze import BronzeCollector

        collector = BronzeCollector()
        assert collector.settings is not None
        assert collector.checkpoint is not None

    @pytest.mark.skip(reason='실제 API 키 필요')
    def test_collect_province_jeju(self):
        """제주도 수집 (실제 실행 시 활성화)"""
        from bareun_pipeline.bronze import BronzeCollector

        collector = BronzeCollector()
        result = collector.collect_province('제주특별자치도', force=False)

        assert 'total_records' in result
        assert result['total_records'] >= 0

    def test_silver_enricher_init(self):
        """Silver 보강기 초기화"""
        from bareun_pipeline.silver.enrich import SilverEnricher

        enricher = SilverEnricher()
        assert hasattr(enricher, 'geocoder')
        assert hasattr(enricher, 'store_verifier')

    def test_gold_loader_init(self):
        """Gold 로더 초기화"""
        from bareun_pipeline.gold.repository import SupabaseLoader

        loader = SupabaseLoader()
        assert loader.client is not None


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
