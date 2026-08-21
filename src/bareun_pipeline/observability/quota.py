"""
통합 QuotaManager (싱글톤)
==============================
- 모든 외부 API 호출의 쿼터 중앙 관리
- 일일 한도 초과 시 조기 차단
- 파일 기반 영속화 (크래시 복구)

기존 참고:
- enrich/official_enricher.py의 QuotaTracker 클래스
- enrich/geocoder.py의 전역 쿼터 관리

보존 사항:
- 일일 한도 관리
- 파일 기반 영속화
- 크래시 복구

개선 사항:
- 싱글톤 패턴 (전역 쿼터 통합 관리)
- 스레드 안전성 (threading.Lock)
- 구조화 로깅
"""
from __future__ import annotations

import json
import os
import threading
from datetime import date
from typing import Dict, Optional, TypedDict, Any

from .logging import get_logger


logger = get_logger(__name__)


class QuotaState(TypedDict):
    """쿼터 상태 구조"""
    date: str
    naver_geo: int
    naver_blog: int
    naver_cafe: int
    naver_news: int
    kakao_local: int


class QuotaExceededError(Exception):
    """쿼터 초과 예외"""
    pass


class QuotaManager:
    """
    통합 쿼터 관리자 (싱글톤)

    사용법:
        qm = QuotaManager.get_instance()
        qm.check_and_consume('naver_geo')
    """

    _instance: Optional['QuotaManager'] = None
    _lock = threading.Lock()

    # 일일 한도 (SSOT)
    DAILY_LIMITS: Dict[str, int] = {
        'naver_geo': 2_000,
        'naver_blog': 8_000,
        'naver_cafe': 5_000,
        'naver_news': 3_000,
        'kakao_local': 20_000,
    }

    def __init__(self, quota_path: Optional[str] = None):
        if quota_path is None:
            from ..settings import get_settings
            settings = get_settings()
            quota_path = os.path.join(settings.cache_dir, 'quota.json')

        self._path = quota_path
        self._state: Dict[str, int] = {}
        self._date: str = ''
        self._state_lock = threading.Lock()
        self._load()

    @classmethod
    def get_instance(cls, quota_path: Optional[str] = None) -> 'QuotaManager':
        """싱글톤 인스턴스 반환"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(quota_path)
        return cls._instance

    def _load(self) -> None:
        """파일에서 쿼터 상태 로드"""
        today = date.today().isoformat()

        if os.path.exists(self._path):
            try:
                with open(self._path, encoding='utf-8') as f:
                    data = json.load(f)
                if data.get('date') == today:
                    self._state = {
                        k: int(v) for k, v in data.items()
                        if k != 'date' and k in self.DAILY_LIMITS
                    }
                    self._date = today
                    logger.info(
                        'quota_loaded',
                        date=today,
                        state=self._state,
                    )
                    return
            except (json.JSONDecodeError, IOError, ValueError) as e:
                logger.warning(
                    'quota_load_failed',
                    error=str(e),
                )

        self._state = {k: 0 for k in self.DAILY_LIMITS}
        self._date = today

    def _save(self) -> None:
        """파일에 쿼터 상태 저장"""
        os.makedirs(os.path.dirname(self._path), exist_ok=True)

        with self._state_lock:
            data: Dict[str, Any] = dict(self._state)
            data['date'] = self._date

        with open(self._path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def check_and_consume(self, provider: str) -> None:
        """
        쿼터 확인 및 소진

        Args:
            provider: API 제공자 이름

        Raises:
            QuotaExceededError: 일일 한도 초과 시
        """
        limit = self.DAILY_LIMITS.get(provider)
        if limit is None:
            return  # 한도 미정의 provider는 통과

        with self._state_lock:
            used = self._state.get(provider, 0)
            if used >= limit:
                raise QuotaExceededError(
                    f'{provider} 쿼터 소진 ({used}/{limit})'
                )
            self._state[provider] = used + 1

            # 200회마다 저장 (I/O 최적화)
            if self._state[provider] % 200 == 0:
                self._save()

    def get_remaining(self, provider: str) -> int:
        """남은 쿼터 반환"""
        limit = self.DAILY_LIMITS.get(provider, 0)
        with self._state_lock:
            used = self._state.get(provider, 0)
        return max(0, limit - used)

    def finalize(self) -> None:
        """최종 저장 (배치 종료 시 호출)"""
        self._save()
        logger.info(
            'quota_finalized',
            summary=self.summary(),
        )

    def summary(self) -> str:
        """쿼터 사용 현황 요약"""
        with self._state_lock:
            parts = []
            for provider, limit in self.DAILY_LIMITS.items():
                used = self._state.get(provider, 0)
                parts.append(f'{provider}: {used}/{limit}')
            return ', '.join(parts)

    def reset(self) -> None:
        """쿼터 초기화 (테스트용)"""
        with self._state_lock:
            self._state = {k: 0 for k in self.DAILY_LIMITS}
        self._save()