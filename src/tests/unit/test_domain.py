"""
도메인 모델 단위 테스트
========================
- CertRecord 검증
- dedup_key 생성
- PII 마스킹
- avoid_tags 생성
"""
from __future__ import annotations

import pytest

from bareun_pipeline.domain.record import CertRecord
from bareun_pipeline.domain.dedup import generate_dedup_key, hash_phone, normalize_name, normalize_address
from bareun_pipeline.domain.privacy import redact_pii, get_pii_fields
from bareun_pipeline.domain.avoid_tags import generate_avoid_tags, get_allergens_from_avoid


class TestCertRecord:
    """CertRecord 모델 테스트"""

    def test_valid_record(self, sample_cert_record):
        """유효한 레코드 생성"""
        assert sample_cert_record.raw_name == '올래국수'
        assert sample_cert_record.is_valid() is True

    def test_invalid_record_no_name(self):
        """이름 없는 레코드는 ValidationError 발생"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError) as exc_info:
            CertRecord(
                source='test',
                cert_type='test',
                raw_name='',
                raw_address='제주특별자치도 제주시',
                is_active=True,
            )

        # raw_name 필드 검증 실패 확인
        errors = exc_info.value.errors()
        assert any(e['loc'] == ('raw_name',) for e in errors)

    def test_invalid_record_cancelled(self, sample_cert_record):
        """취소된 레코드는 유효하지 않음"""
        from datetime import date
        sample_cert_record.cancelled_at = date(2025, 1, 1)
        assert sample_cert_record.is_valid() is False

    def test_phone_normalization(self):
        """전화번호 정규화"""
        record = CertRecord(
            source='test',
            cert_type='test',
            raw_name='테스트',
            raw_address='테스트 주소',
            phone='02-1234-5678',
        )
        assert record.phone == '0212345678'

    def test_phone_masked_number(self):
        """마스킹된 전화번호는 None으로 처리"""
        record = CertRecord(
            source='test',
            cert_type='test',
            raw_name='테스트',
            raw_address='테스트 주소',
            phone='02-****-5678',
        )
        assert record.phone is None

    def test_date_parsing(self):
        """날짜 파싱"""
        record = CertRecord(
            source='test',
            cert_type='test',
            raw_name='테스트',
            raw_address='테스트 주소',
            designated_at='2025-01-15',
        )
        from datetime import date
        assert record.designated_at == date(2025, 1, 15)

    def test_date_parsing_compact(self):
        """날짜 파싱 (YYYYMMDD 형식)"""
        record = CertRecord(
            source='test',
            cert_type='test',
            raw_name='테스트',
            raw_address='테스트 주소',
            designated_at='20250115',
        )
        from datetime import date
        assert record.designated_at == date(2025, 1, 15)

    def test_list_normalization(self):
        """리스트 정규화"""
        record = CertRecord(
            source='test',
            cert_type='test',
            raw_name='테스트',
            raw_address='테스트 주소',
            menus='김치찌개,된장찌개',
        )
        assert record.menus == ['김치찌개,된장찌개']

    def test_list_from_string(self):
        """문자열을 리스트로 변환"""
        record = CertRecord(
            source='test',
            cert_type='test',
            raw_name='테스트',
            raw_address='테스트 주소',
            certs='food_safety',
        )
        assert record.certs == ['food_safety']


class TestDedupKey:
    """dedup_key 생성 테스트"""

    def test_generate_dedup_key(self):
        """dedup_key 생성"""
        key = generate_dedup_key(
            raw_name='올래국수',
            raw_address='제주특별자치도 제주시 중앙로 1길 10',
            phone='0641234567',
        )
        assert len(key) == 32
        assert isinstance(key, str)

    def test_dedup_key_deterministic(self):
        """dedup_key는 결정적이어야 함"""
        key1 = generate_dedup_key(
            raw_name='올래국수',
            raw_address='제주특별자치도 제주시 중앙로 1길 10',
        )
        key2 = generate_dedup_key(
            raw_name='올래국수',
            raw_address='제주특별자치도 제주시 중앙로 1길 10',
        )
        assert key1 == key2

    def test_dedup_key_different_for_different_names(self):
        """다른 이름은 다른 dedup_key 생성"""
        key1 = generate_dedup_key(
            raw_name='올래국수',
            raw_address='제주특별자치도 제주시 중앙로 1길 10',
        )
        key2 = generate_dedup_key(
            raw_name='우진해장국',
            raw_address='제주특별자치도 제주시 중앙로 1길 10',
        )
        assert key1 != key2

    def test_hash_phone(self):
        """전화번호 해시화"""
        hashed = hash_phone('0641234567')
        assert len(hashed) == 16
        assert '0641234567' not in hashed

    def test_normalize_name(self):
        """상호명 정규화"""
        assert normalize_name('올래국수') == '올래국수'
        assert normalize_name('올래 국수!') == '올래국수'
        assert normalize_name('OLAE NOODLE') == 'olaenoodle'

    def test_normalize_address(self):
        """주소 정규화"""
        assert normalize_address('제주특별자치도 제주시 (중앙로)') == '제주특별자치도제주시'


class TestPrivacy:
    """PII 마스킹 테스트"""

    def test_redact_pii(self):
        """PII 필드 마스킹"""
        rows = [
            {'PRSDNT_NM': '홍길동', 'BSSH_NM': '올래국수'},
        ]
        masked = redact_pii(rows, 'food_safety')
        assert masked[0]['PRSDNT_NM'] == '[비식별화]'
        assert masked[0]['BSSH_NM'] == '올래국수'

    def test_get_pii_fields(self):
        """PII 필드 목록 조회"""
        fields = get_pii_fields('food_safety')
        assert 'PRSDNT_NM' in fields
        assert 'WRKR_REG_NO' in fields


class TestAvoidTags:
    """avoid_tags 생성 테스트"""

    def test_generate_avoid_tags(self):
        """avoid_tags 생성"""
        tags = generate_avoid_tags(['갑각류·조개류', '육류'])
        assert '갑각류' in tags
        assert '조개류' in tags
        assert '육류' in tags

    def test_generate_avoid_tags_empty(self):
        """빈 allergen_tags"""
        tags = generate_avoid_tags([])
        assert tags == []

    def test_get_allergens_from_avoid(self):
        """avoid_tags에서 allergen 역추적"""
        allergens = get_allergens_from_avoid(['갑각류', '육류'])
        assert '갑각류·조개류' in allergens
        assert '육류' in allergens