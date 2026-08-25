"""
Silver Layer: 메뉴 추출 (NER + LLM 하이브리드)
================================================
⚠️ 주의: 이 파일은 silver/enrich/menu.py와 다릅니다!

비교:
- silver/enrich/menu.py: 공식 API(네이버/카카오)에서 메뉴 보강 (현재 사용 중)
- silver/menu_extract.py: 블로그/리뷰 텍스트에서 메뉴 추출 (Phase 4 예정)

사용 시점:
- Phase 1~3: enrich/menu.py만 사용
- Phase 4: menu_extract.py 활성화 (블로그/리뷰 데이터 수집 시작 시)

로드맵:
- Phase 4-A: 블로그/리뷰 수집 어댑터 구현 (adapters/blog_review/)
- Phase 4-B: menu_extract.py의 extract_for_records() 완성
- Phase 4-C: enrich/menu.py와 결과 병합 로직 구현
"""

from __future__ import annotations

import asyncio

from ..domain.menu import deduplicate_menus, is_valid_menu_name
from ..domain.record import CertRecord
from ..observability.logging import get_logger
from ..settings import get_settings

logger = get_logger(__name__)


class MenuExtractor:
    """
    메뉴 추출기 (NER + LLM)

    사용법:
        extractor = MenuExtractor()
        result = extractor.extract_from_text('올래국수에서 고기국수를 먹었어요', '올래국수')
    """

    def __init__(self):
        self.settings = get_settings()
        self.ner_pipeline = None
        self.llm_verifier = None
        self._initialized = False

    def _ensure_initialized(self) -> None:
        """지연 초기화"""
        if self._initialized:
            return

        # NER 파이프라인 초기화
        try:
            from ..nlp.ner.predictor import MenuNERPredictor

            self.ner_pipeline = MenuNERPredictor()
            logger.info('menu_extractor_ner_initialized')
        except Exception as e:
            logger.warning(
                'menu_extractor_ner_init_failed',
                error=str(e),
            )

        # LLM 검증기 초기화
        try:
            from ..nlp.llm.verifier import LLMVerifier

            self.llm_verifier = LLMVerifier()
            logger.info('menu_extractor_llm_initialized')
        except Exception as e:
            logger.warning(
                'menu_extractor_llm_init_failed',
                error=str(e),
            )

        self._initialized = True

    def extract_from_text(self, text: str, store_name: str) -> dict:
        """
        텍스트에서 메뉴 추출

        Args:
            text: 입력 텍스트
            store_name: 가게명

        Returns:
            {'menus': [...], 'confidence': float, 'method': str}
        """
        self._ensure_initialized()

        if not text or not text.strip():
            return {'menus': [], 'confidence': 0.0, 'method': 'empty'}

        # NER 추출 시도
        if self.ner_pipeline:
            try:
                result = self.ner_pipeline.predict(text, store_name=store_name)
                raw_menus = result.get('menus', [])

                # 🆕 공통 유틸리티로 필터링
                valid_menus = [m for m in raw_menus if is_valid_menu_name(m, store_name)]
                valid_menus = deduplicate_menus(valid_menus)

                if valid_menus:
                    confidence = min(len(valid_menus) * 0.3, 1.0)
                    return {
                        'menus': valid_menus,
                        'confidence': confidence,
                        'method': 'ner',
                    }
            except Exception as e:
                logger.error('menu_extract_ner_failed', error=str(e))

        # LLM 추출 시도 (NER 실패 시)
        if self.llm_verifier and self.llm_verifier.available:
            try:
                menus = asyncio.run(self.llm_verifier.extract_menu_direct(text, store_name))
                if menus:
                    return {
                        'menus': menus,
                        'confidence': 0.8,
                        'method': 'llm',
                    }
            except Exception as e:
                logger.error(
                    'menu_extract_llm_failed',
                    error=str(e),
                )

        return {'menus': [], 'confidence': 0.0, 'method': 'none'}

    async def extract_for_records(
        self,
        records: list[CertRecord],
    ) -> list[CertRecord]:
        """
        레코드 목록에 대해 메뉴 추출

        Args:
            records: 메뉴 추출할 레코드 목록

        Returns:
            메뉴 추출된 레코드 목록
        """
        self._ensure_initialized()

        # main_menu가 없는 레코드만 처리
        missing = [r for r in records if not r.main_menu]

        if not missing:
            logger.info('menu_extract_skip_all_have_menu')
            return records

        logger.info(
            'menu_extract_start',
            missing=len(missing),
            total=len(records),
        )

        # 현재는 placeholder (Phase 4에서 official_enricher 연동)
        # 기존 enrich/official_enricher.py를 여기에서 호출하도록 수정 예정

        logger.info('menu_extract_complete', count=len(records))

        return records
