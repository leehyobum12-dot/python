"""
TouristInfo 체크포인트 시스템
==============================
- contentid 기반 증분 업데이트
- 매일 API 호출 최소화 (신규만 추가)
- 체크포인트 파일로 상태 관리

사용법:
    checkpoint = TouristCheckpoint('/path/to/cache')
    existing_ids = checkpoint.get_existing_ids()
    
    # 신규 데이터만 수집
    new_records = [r for r in all_records if r.content_id not in existing_ids]
    
    # 체크포인트 업데이트
    checkpoint.update([r.content_id for r in new_records])
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import List, Set, Dict

from ..observability.logging import get_logger

logger = get_logger(__name__)


class TouristCheckpoint:
    """TouristInfo 체크포인트 관리자"""
    
    def __init__(self, checkpoint_dir: str):
        self.checkpoint_dir = checkpoint_dir
        self.checkpoint_file = os.path.join(checkpoint_dir, 'tourist_checkpoint.json')
        os.makedirs(checkpoint_dir, exist_ok=True)
    
    def load(self) -> Dict:
        """체크포인트 로드"""
        if not os.path.exists(self.checkpoint_file):
            return {
                'content_ids': [],
                'last_update': None,
                'total_count': 0,
            }
        
        try:
            with open(self.checkpoint_file, encoding='utf-8') as f:
                data = json.load(f)
                logger.info(
                    'tourist_checkpoint_loaded',
                    count=len(data.get('content_ids', [])),
                    last_update=data.get('last_update'),
                )
                return data
        except Exception as e:
            logger.warning('tourist_checkpoint_load_failed', error=str(e))
            return {
                'content_ids': [],
                'last_update': None,
                'total_count': 0,
            }
    
    def save(self, content_ids: List[int]) -> None:
        """체크포인트 저장"""
        checkpoint = {
            'content_ids': content_ids,
            'last_update': datetime.now().isoformat(),
            'total_count': len(content_ids),
        }
        
        try:
            with open(self.checkpoint_file, 'w', encoding='utf-8') as f:
                json.dump(checkpoint, f, ensure_ascii=False, indent=2)
            logger.info(
                'tourist_checkpoint_saved',
                count=len(content_ids),
            )
        except Exception as e:
            logger.error('tourist_checkpoint_save_failed', error=str(e))
    
    def get_existing_ids(self) -> Set[int]:
        """기존 content_id 집합 반환"""
        checkpoint = self.load()
        return set(checkpoint.get('content_ids', []))
    
    def is_new(self, content_id: int) -> bool:
        """신규 content_id 여부"""
        existing = self.get_existing_ids()
        return content_id not in existing
    
    def update(self, new_content_ids: List[int]) -> None:
        """체크포인트 업데이트 (기존 + 신규 합침)"""
        checkpoint = self.load()
        existing_ids = set(checkpoint.get('content_ids', []))
        updated_ids = list(existing_ids | set(new_content_ids))
        self.save(updated_ids)
    
    def get_stats(self) -> Dict:
        """체크포인트 통계 반환"""
        checkpoint = self.load()
        return {
            'total_count': len(checkpoint.get('content_ids', [])),
            'last_update': checkpoint.get('last_update'),
        }