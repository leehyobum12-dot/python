"""
바른인증식당 데이터 파이프라인
================================
공공데이터 수집/정제/적재 자동화 파이프라인입니다.

아키텍처:
    [Adapters] → [Bronze] → [Silver] → [Gold] → [Supabase]
       수집         원본 보존    정제/병합    DB 매칭     적재

사용법:
    from bareun_pipeline.settings import get_settings
    from bareun_pipeline.domain import CertRecord
    from bareun_pipeline.bronze import BronzeCollector
    from bareun_pipeline.silver import merge_records
    from bareun_pipeline.gold import SupabaseLoader

주의:
    이 패키지는 루트에서 아무것도 즉시 로드하지 않습니다.
    무거운 의존성(torch, transformers, supabase, pyahocorasick)은
    하위 패키지에서 실제로 사용할 때만 로드됩니다.
"""

__version__ = '1.0.0'
__author__ = '주니어개발자'