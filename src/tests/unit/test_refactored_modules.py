"""
리팩토링된 모듈 종합 검증 테스트
=================================
- Import 검증
- 핵심 로직 단위 테스트
- 타입 안정성 확인
"""

from datetime import date

import pytest

# ═══════════════════════════════════════════════════════════
# 1. Import 검증 (가장 기본)
# ═══════════════════════════════════════════════════════════


class TestImports:
    """모든 리팩토링된 모듈이 정상 임포트되는지 검증"""

    def test_domain_address_import(self):
        """주소 도메인 모듈 임포트"""
        from bareun_pipeline.domain.address import (
            canon_province,
            parse_addr_tokens,
        )

        assert callable(parse_addr_tokens)
        assert callable(canon_province)

    def test_adapters_base_import(self):
        """어댑터 베이스 모듈 임포트"""
        from bareun_pipeline.adapters.base import (
            create_session,
        )

        assert callable(create_session)

    def test_adapters_get_all(self):
        """전체 어댑터 동적 로드"""
        from bareun_pipeline.adapters import get_all_adapters

        adapters = get_all_adapters()
        assert len(adapters) >= 6  # 최소 6개 어댑터
        assert all(hasattr(a, 'source') for a in adapters)

    def test_silver_enrich_import(self):
        """Silver 보강 패키지 임포트"""
        from bareun_pipeline.silver.enrich import SilverEnricher

        enricher = SilverEnricher()
        assert hasattr(enricher, 'enrich')

    def test_gold_repository_import(self):
        """Gold repository 패키지 임포트"""
        from bareun_pipeline.gold.repository import (
            load_to_supabase,
        )

        assert callable(load_to_supabase)

    def test_bronze_collector_import(self):
        """Bronze 수집기 임포트"""
        from bareun_pipeline.bronze import BronzeCollector

        collector = BronzeCollector()
        assert hasattr(collector, 'collect_province')


# ═══════════════════════════════════════════════════════════
# 2. 주소 파싱 테스트
# ═══════════════════════════════════════════════════════════


class TestAddressParsing:
    """주소 파싱 로직 검증"""

    def test_parse_full_address(self):
        """전체 주소 파싱"""
        from bareun_pipeline.domain.address import parse_addr_tokens

        province, municipality, emd = parse_addr_tokens('제주특별자치도 서귀포시 서홍동 123-4')
        assert province == '제주특별자치도'
        assert municipality == '서귀포시'
        assert emd == '서홍동'

    def test_parse_road_address(self):
        """도로명 주소 파싱"""
        from bareun_pipeline.domain.address import parse_addr_tokens

        province, municipality, emd = parse_addr_tokens('서울특별시 강남구 테헤란로 123')
        assert province == '서울특별시'
        assert municipality == '강남구'

    def test_canon_province(self):
        """시도명 표준화"""
        from bareun_pipeline.domain.address import canon_province

        assert canon_province('제주도') == '제주특별자치도'
        assert canon_province('서울') == '서울특별시'
        assert canon_province('전북') == '전북특별자치도'

    def test_province_alias_2026(self):
        """2026년 행정구역 기준 검증"""
        from bareun_pipeline.domain.address import PROVINCE_CANON

        # 강원특별자치도, 전북특별자치도 확인
        assert '강원특별자치도' in PROVINCE_CANON.values()
        assert '전북특별자치도' in PROVINCE_CANON.values()

        # 전남광주통합특별시는 제거되어야 함
        assert '전남광주통합특별시' not in PROVINCE_CANON.values()


# ═══════════════════════════════════════════════════════════
# 3. 병합 로직 테스트
# ═══════════════════════════════════════════════════════════


class TestMergeLogic:
    """Silver 병합 로직 검증"""

    def test_merger_instantiation(self):
        """Merger 인스턴스 생성"""
        from bareun_pipeline.silver.merge import Merger

        merger = Merger(matching_radius_m=500)
        assert merger.matching_radius_m == 500

    def test_name_similarity(self):
        """이름 유사도 계산"""
        from bareun_pipeline.silver.merge import Merger

        merger = Merger()
        sim = merger._name_similarity('제주옥돔구이', '제주옥돔구이전문점')
        assert 0.5 < sim <= 1.0

    def test_dedup_key_generation(self):
        """dedup_key 생성"""
        from bareun_pipeline.domain.dedup import generate_dedup_key

        key1 = generate_dedup_key(
            raw_name='제주옥돔구이',
            raw_address='제주특별자치도 서귀포시 서홍동 123-4',
        )
        key2 = generate_dedup_key(
            raw_name='제주옥돔구이',
            raw_address='제주특별자치도 서귀포시 서홍동 123-4',
        )
        assert key1 == key2  # 동일 입력 → 동일 키


# ═══════════════════════════════════════════════════════════
# 4. Gold Mapper 테스트
# ═══════════════════════════════════════════════════════════


class TestGoldMapper:
    """Gold mapper 변환 로직 검증"""

    def test_parse_date_formats(self):
        """날짜 파싱 (다양한 포맷)"""
        from bareun_pipeline.gold.repository.mapper import _parse_date

        # YYYYMMDD
        assert _parse_date('20250115') == date(2025, 1, 15)
        # YYYY-MM-DD
        assert _parse_date('2025-01-15') == date(2025, 1, 15)
        # 유효하지 않은 날짜
        assert _parse_date('invalid') is None
        assert _parse_date(None) is None

    def test_operational_status_derivation(self):
        """운영상태 파생 계산"""
        from bareun_pipeline.domain.record import CertRecord
        from bareun_pipeline.gold.repository.mapper import _derive_operational_status

        # 활성 레코드
        active_record = CertRecord(
            source='test',
            cert_type='test',
            raw_name='테스트식당',
            raw_address='서울시 강남구',
            is_active=True,
        )
        assert _derive_operational_status(active_record) == 'operational'

        # 폐업 레코드
        closed_record = CertRecord(
            source='test',
            cert_type='test',
            raw_name='테스트식당',
            raw_address='서울시 강남구',
            is_active=False,
        )
        assert _derive_operational_status(closed_record) == 'permanently_closed'

    def test_pii_masking(self):
        """PII 마스킹 (전화번호)"""
        from bareun_pipeline.domain.privacy import hash_phone

        hashed = hash_phone('010-1234-5678')
        assert hashed != '010-1234-5678'
        assert len(hashed) > 20  # SHA-256 해시 길이


# ═══════════════════════════════════════════════════════════
# 5. 설정 검증
# ═══════════════════════════════════════════════════════════


class TestSettings:
    """설정 모듈 검증"""

    def test_settings_instantiation(self):
        """Settings 인스턴스 생성"""
        from bareun_pipeline.settings import get_settings

        settings = get_settings()
        assert hasattr(settings, 'supabase_url')
        assert hasattr(settings, 'data_dir')

    def test_provinces_list(self):
        """시도 목록 검증"""
        from bareun_pipeline.settings import get_settings

        settings = get_settings()
        assert len(settings.provinces) == 17
        assert '제주특별자치도' in settings.provinces


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
