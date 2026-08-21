"""
Bronze Layer 패키지
====================
원본 데이터 수집과 보존을 담당합니다.

Bronze Layer의 원칙:
1. 원본 데이터는 절대 수정하지 않음 (불변 스냅샷)
2. PII 마스킹 후 저장 (개인정보보호법 준수)
3. 모든 원본은 raw_certifications 테이블에 INSERT (감사 추적)
4. 시도별 체크포인트로 부분 실패 복구

모듈:
- collect: 수집 orchestration (어댑터 순회)
- raw_archiver: 원본 데이터 보존 (NDJSON + Supabase)
"""
from .collect import BronzeCollector
from .raw_archiver import RawArchiver

__all__ = [
    'BronzeCollector',
    'RawArchiver',
]