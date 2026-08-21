"""
IO 모듈 단위 테스트
====================
- 체크포인트 관리
"""
from __future__ import annotations

import os
import tempfile
import pytest

from bareun_pipeline.io.checkpoint import CheckpointManager


class TestCheckpointManager:
    """체크포인트 관리 테스트"""

    def test_mark_completed(self):
        """완료 마커 저장"""
        with tempfile.TemporaryDirectory() as tmpdir:
            cp = CheckpointManager(tmpdir)
            cp.mark_completed('제주특별자치도', 1234)

            assert cp.is_completed('제주특별자치도') is True

    def test_is_completed_false(self):
        """완료되지 않은 시도"""
        with tempfile.TemporaryDirectory() as tmpdir:
            cp = CheckpointManager(tmpdir)

            assert cp.is_completed('서울특별시') is False

    def test_reset(self):
        """체크포인트 초기화"""
        with tempfile.TemporaryDirectory() as tmpdir:
            cp = CheckpointManager(tmpdir)
            cp.mark_completed('제주특별자치도', 1234)
            cp.reset('제주특별자치도')

            assert cp.is_completed('제주특별자치도') is False

    def test_get_record_count(self):
        """레코드 수 조회"""
        with tempfile.TemporaryDirectory() as tmpdir:
            cp = CheckpointManager(tmpdir)
            cp.mark_completed('제주특별자치도', 1234)

            assert cp.get_record_count('제주특별자치도') == 1234