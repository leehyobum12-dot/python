"""
NLP: NER 파이프라인 통합 모듈
===============================
- NER 추출 → 검증 → 실전 데이터 로깅
- 신뢰도 기반 분기
- 배치 버퍼링 로깅 (이벤트 루프 차단 방지)

기존 참고:
- ner/core/ner_pipeline.py 전체 구조
- 실전 데이터 로깅 (재학습용)

보존 사항:
- NER 추출 → 검증 파이프라인
- 실전 데이터 로깅
- 신뢰도 계산

개선 사항:
- 배치 버퍼링 로깅 (동기 파일 쓰기 방지)
- 구조화 로깅
- 타입 안전성 강화
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import List, Dict, Optional

from .predictor import MenuNERPredictor
from ...settings import get_settings
from ...observability.logging import get_logger


logger = get_logger(__name__)


class NERMenuPipeline:
    """
    NER 기반 메뉴 추출 파이프라인

    사용법:
        pipeline = NERMenuPipeline()
        result = pipeline.extract_menus_from_text('올래국수에서 고기국수를 먹었어요', '올래국수')
    """

    # 로깅 버퍼 크기 (이벤트 루프 차단 방지)
    LOG_BUFFER_SIZE = 100

    def __init__(self):
        self.settings = get_settings()
        self.predictor = MenuNERPredictor()
        self.log_count = 0
        self._log_buffer: List[Dict] = []

        # 로깅 디렉토리 생성
        os.makedirs(self.settings.data_dir, exist_ok=True)

        logger.info('ner_pipeline_initialized')

    def extract_menus_from_text(
        self,
        text: str,
        store_name: str,
        source: str = 'blog'
    ) -> Dict:
        """
        텍스트에서 메뉴 추출 (NER + 후처리)

        기존 ner/core/ner_pipeline.py의 extract_menus_from_text() 함수 보존

        Args:
            text: 입력 텍스트
            store_name: 가게명
            source: 출처 (blog, cafe, news 등)

        Returns:
            {
                'main_menu': 대표메뉴,
                'sub_menus': [서브메뉴],
                'all_menus': [추출된 모든 메뉴],
                'confidence': 신뢰도,
            }
        """
        if not text or not text.strip():
            return {
                'main_menu': None,
                'sub_menus': [],
                'all_menus': [],
                'confidence': 0.0,
            }

        # NER 추출
        result = self.predictor.predict(text, store_name=store_name)
        menus = result.get('menus', [])

        # 메뉴 필터링 (가게 이름과 동일한 메뉴 제외)
        filtered_menus = [
            m for m in menus if m != store_name and len(m) >= 2
        ]

        # 대표메뉴 선택
        main_menu = filtered_menus[0] if filtered_menus else None

        # 서브메뉴
        sub_menus = filtered_menus[1:6] if len(filtered_menus) > 1 else []

        # 신뢰도 계산 (메뉴 개수 기반)
        confidence = min(len(filtered_menus) * 0.3, 1.0)

        # 실전 데이터 로깅 (재학습용)
        self._log_real_data(text, store_name, source, filtered_menus)

        return {
            'main_menu': main_menu,
            'sub_menus': sub_menus,
            'all_menus': filtered_menus,
            'confidence': confidence,
        }

    def _log_real_data(
        self,
        text: str,
        store_name: str,
        source: str,
        menus: List[str]
    ) -> None:
        """
        실전 데이터 로깅 (재학습용)

        기존 ner/core/ner_pipeline.py의 _log_real_data() 함수 보존
        개선: 배치 버퍼링으로 동기 파일 쓰기 방지

        Args:
            text: 입력 텍스트
            store_name: 가게명
            source: 출처
            menus: 추출된 메뉴
        """
        try:
            log_entry = {
                'timestamp': datetime.now().isoformat(),
                'text': text[:500],
                'store_name': store_name,
                'source': source,
                'extracted_menus': menus,
            }

            self._log_buffer.append(log_entry)
            self.log_count += 1

            # 버퍼가 가득 차면 플러시
            if len(self._log_buffer) >= self.LOG_BUFFER_SIZE:
                self._flush_log_buffer()

            if self.log_count % 100 == 0:
                logger.info(
                    'ner_real_data_logged',
                    count=self.log_count,
                )

        except Exception as e:
            logger.warning(
                'ner_real_data_log_failed',
                error=str(e),
            )

    def _flush_log_buffer(self) -> None:
        """로그 버퍼 플러시"""
        if not self._log_buffer:
            return

        log_path = os.path.join(
            self.settings.data_dir,
            'real_data_log.jsonl'
        )

        try:
            with open(log_path, 'a', encoding='utf-8') as f:
                for entry in self._log_buffer:
                    f.write(json.dumps(entry, ensure_ascii=False) + '\n')

            logger.debug(
                'ner_log_buffer_flushed',
                count=len(self._log_buffer),
            )

            self._log_buffer.clear()

        except Exception as e:
            logger.warning(
                'ner_log_flush_failed',
                error=str(e),
            )

    def close(self) -> None:
        """파이프라인 종료 (버퍼 플러시)"""
        self._flush_log_buffer()
        logger.info('ner_pipeline_closed', total_logged=self.log_count)

    def __del__(self):
        """소멸자에서 버퍼 플러시"""
        try:
            self._flush_log_buffer()
        except Exception:
            pass