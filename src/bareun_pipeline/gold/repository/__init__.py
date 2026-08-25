"""
Bareun Pipeline: Gold Repository Package
=========================================
- Supabase 적재 관련 모듈의 통합 진입점
- 무거운 오케스트레이션 로직은 loader.py로 이관됨
"""

from __future__ import annotations

from .client import SupabaseClient, SupabaseClientError
from .loader import SupabaseLoader, load_to_supabase
from .mapper import to_db_row, to_db_rows

__all__ = [
    'SupabaseClient',
    'SupabaseClientError',
    'SupabaseLoader',
    'load_to_supabase',
    'to_db_row',
    'to_db_rows',
]
