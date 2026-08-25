"""
TouristInfo 체크포인트 시스템 (Type Safe)
==========================================
- contentid 기반 증분 업데이트
- 매일 API 호출 최소화 (신규만 추가)
- 체크포인트 파일로 상태 관리

타입 안전성 강화 (v2.0):
- TypedDict로 체크포인트 구조 명시
- 구체적 컬렉션 타입 선언
- UTC 타임존 일관성 확보
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any, TypedDict

from ..observability.logging import get_logger

logger = get_logger(__name__)


# 🆕 체크포인트 구조를 TypedDict로 명시적 정의
class CheckpointData(TypedDict):
    """체크포인트 파일의 구조 정의"""

    content_ids: list[int]
    last_update: str | None
    total_count: int


class TouristCheckpoint:
    """TouristInfo 체크포인트 관리자"""

    def __init__(self, checkpoint_dir: str) -> None:
        self.checkpoint_dir = checkpoint_dir
        self.checkpoint_file = os.path.join(checkpoint_dir, 'tourist_checkpoint.json')
        os.makedirs(checkpoint_dir, exist_ok=True)

    def load(self) -> CheckpointData:
        """체크포인트 로드"""
        if not os.path.exists(self.checkpoint_file):
            return {
                'content_ids': [],
                'last_update': None,
                'total_count': 0,
            }

        try:
            with open(self.checkpoint_file, encoding='utf-8') as f:
                # 🆕 json.load() 결과를 구체적 타입으로 캐스팅
                raw_data: dict[str, Any] = json.load(f)

                # 🆕 TypedDict 형태로 정규화 (키 부재 방어)
                data: CheckpointData = {
                    'content_ids': [
                        int(x) for x in raw_data.get('content_ids', []) if isinstance(x, (int, str))
                    ],
                    'last_update': (
                        str(raw_data['last_update']) if raw_data.get('last_update') else None
                    ),
                    'total_count': int(raw_data.get('total_count', 0)),
                }

                logger.info(
                    'tourist_checkpoint_loaded',
                    count=len(data['content_ids']),
                    last_update=data['last_update'],
                )
                return data

        except (OSError, json.JSONDecodeError, ValueError) as e:
            logger.warning(
                'tourist_checkpoint_load_failed',
                error=str(e),
                error_type=type(e).__name__,
            )
            return {
                'content_ids': [],
                'last_update': None,
                'total_count': 0,
            }

    def save(self, content_ids: list[int]) -> None:
        """체크포인트 저장"""
        checkpoint: CheckpointData = {
            'content_ids': content_ids,
            # 🆕 UTC 타임존 적용 (raw_archiver.py와 일관성)
            'last_update': datetime.now(UTC).isoformat(),
            'total_count': len(content_ids),
        }

        try:
            with open(self.checkpoint_file, 'w', encoding='utf-8') as f:
                json.dump(checkpoint, f, ensure_ascii=False, indent=2)
            logger.info(
                'tourist_checkpoint_saved',
                count=len(content_ids),
            )
        except OSError as e:
            logger.error(
                'tourist_checkpoint_save_failed',
                error=str(e),
                error_type=type(e).__name__,
            )

    def get_existing_ids(self) -> set[int]:
        """기존 content_id 집합 반환"""
        checkpoint = self.load()
        # 🆕 구체적 타입 선언
        ids: list[int] = checkpoint.get('content_ids', [])
        return set(ids)

    def is_new(self, content_id: int) -> bool:
        """신규 content_id 여부"""
        existing = self.get_existing_ids()
        return content_id not in existing

    def update(self, new_content_ids: list[int]) -> None:
        """체크포인트 업데이트 (기존 + 신규 합침)"""
        checkpoint = self.load()
        # 🆕 구체적 타입 선언
        existing_list: list[int] = checkpoint.get('content_ids', [])
        existing_ids: set[int] = set(existing_list)
        updated_ids: list[int] = list(existing_ids | set(new_content_ids))
        self.save(updated_ids)

    def get_stats(self) -> dict[str, Any]:
        """체크포인트 통계 반환"""
        checkpoint = self.load()
        return {
            'total_count': len(checkpoint.get('content_ids', [])),
            'last_update': checkpoint.get('last_update'),
        }
