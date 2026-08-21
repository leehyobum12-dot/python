"""
시도별 체크포인트 관리
========================
- 전국 16개 시도 독립 관리
- 크래시 시 해당 시도만 재실행
- Freshness 검증 (24시간 이내만 유효)

기존 참고:
- storage/checkpoint.py의 체크포인트 관리 로직
- pipeline.py의 _check_marker_freshness() 함수

보존 사항:
- 시도별 독립 체크포인트
- Freshness 검증
- JSON 메타데이터 저장

개선 사항:
- CheckpointManager 클래스로 캡슐화
- 타입 안전성 강화
- 구조화 로깅
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Optional, TypedDict

from ..observability.logging import get_logger


logger = get_logger(__name__)


class CheckpointData(TypedDict):
    """체크포인트 데이터 구조"""
    province: str
    completed_at: str
    record_count: int


class CheckpointManager:
    """
    시도별 체크포인트 관리자

    사용법:
        cp = CheckpointManager('/path/to/checkpoints')
        cp.mark_completed('제주특별자치도', 1234)
        is_done = cp.is_completed('제주특별자치도')
    """

    def __init__(self, checkpoint_dir: str):
        self._dir = checkpoint_dir
        os.makedirs(checkpoint_dir, exist_ok=True)

    def _path(self, province: str) -> str:
        """체크포인트 파일 경로"""
        safe_name = province.replace('/', '_').replace('\\', '_')
        return os.path.join(self._dir, f'{safe_name}.checkpoint.json')

    def is_completed(
        self,
        province: str,
        max_age_hours: int = 24,
    ) -> bool:
        """
        완료 여부 확인 (Freshness 검증 포함)

        Args:
            province: 시도명
            max_age_hours: 최대 유효 시간 (기본 24시간)

        Returns:
            True이면 완료 및 유효
        """
        path = self._path(province)
        if not os.path.exists(path):
            return False

        try:
            with open(path, encoding='utf-8') as f:
                data: CheckpointData = json.load(f)

            completed_at = datetime.fromisoformat(data['completed_at'])
            age_hours = (datetime.now() - completed_at).total_seconds() / 3600

            if age_hours <= max_age_hours:
                logger.debug(
                    'checkpoint_valid',
                    province=province,
                    age_hours=age_hours,
                )
                return True
            else:
                logger.debug(
                    'checkpoint_expired',
                    province=province,
                    age_hours=age_hours,
                    max_age_hours=max_age_hours,
                )
                return False

        except (json.JSONDecodeError, ValueError, KeyError) as e:
            logger.warning(
                'checkpoint_parse_error',
                province=province,
                error=str(e),
            )
            return False

    def mark_completed(
        self,
        province: str,
        record_count: int,
    ) -> None:
        """
        완료 마커 저장

        Args:
            province: 시도명
            record_count: 수집된 레코드 수
        """
        path = self._path(province)
        data: CheckpointData = {
            'province': province,
            'completed_at': datetime.now().isoformat(),
            'record_count': record_count,
        }

        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logger.info(
            'checkpoint_marked',
            province=province,
            record_count=record_count,
        )

    def get_record_count(self, province: str) -> Optional[int]:
        """저장된 레코드 수 반환"""
        path = self._path(province)
        if not os.path.exists(path):
            return None

        try:
            with open(path, encoding='utf-8') as f:
                data: CheckpointData = json.load(f)
            return data.get('record_count')
        except (json.JSONDecodeError, KeyError):
            return None

    def reset(self, province: str) -> None:
        """체크포인트 초기화 (재실행 시)"""
        path = self._path(province)
        if os.path.exists(path):
            os.remove(path)
            logger.info('checkpoint_reset', province=province)

    def reset_all(self) -> None:
        """모든 체크포인트 초기화"""
        for f in os.listdir(self._dir):
            if f.endswith('.checkpoint.json'):
                os.remove(os.path.join(self._dir, f))
        logger.info('checkpoint_reset_all')

    def list_all(self) -> dict[str, CheckpointData]:
        """모든 체크포인트 목록 반환"""
        result: dict[str, CheckpointData] = {}
        for f in os.listdir(self._dir):
            if f.endswith('.checkpoint.json'):
                path = os.path.join(self._dir, f)
                try:
                    with open(path, encoding='utf-8') as fp:
                        data: CheckpointData = json.load(fp)
                    result[data['province']] = data
                except (json.JSONDecodeError, KeyError):
                    continue
        return result