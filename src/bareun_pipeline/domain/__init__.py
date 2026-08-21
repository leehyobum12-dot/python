"""
도메인 패키지
==============
비즈니스 규칙과 도메인 모델을 정의합니다.

모듈:
- record: CertRecord Pydantic 모델 (SSOT)
- dedup: dedup_key 생성 (salted SHA-256)
- privacy: PII 마스킹
- avoid_tags: avoid_tags 자동 생성
- taxonomy: TaxonomyRegistry (7차원 카테고리)
- constants: NLP 상수 SSOT

이 패키지의 모듈은 모두 가볍습니다 (Pydantic, 상수).
즉시 노출 방식을 유지합니다.
"""
from .record import CertRecord
from .dedup import generate_dedup_key, hash_phone
from .privacy import redact_pii, get_pii_fields
from .avoid_tags import generate_avoid_tags

__all__ = [
    'CertRecord',
    'generate_dedup_key',
    'hash_phone',
    'redact_pii',
    'get_pii_fields',
    'generate_avoid_tags',
]