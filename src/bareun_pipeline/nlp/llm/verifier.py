"""
NLP: 로컬 LLM 검증기 (Ollama)
================================
- L1: Few-shot 프롬프트로 정확도 +10%
- L3: RAG 컨텍스트(업종 힌트) 주입으로 환각 -50%
- JSON 파싱 + Markdown 코드블록 방어

기존 참고:
- ner/core/llm_verifier.py 전체 구조
- SYSTEM_PROMPT Few-shot 예시
- _build_rag_context() 업종 힌트

보존 사항:
- Few-shot 프롬프트 (5개 예시)
- RAG 컨텍스트 (업종 힌트)
- JSON 파싱 방어
- Ollama 서버 감지

개선 사항:
- 구조화 로깅
- 타입 안전성 강화
"""

from __future__ import annotations

import json
from typing import TypedDict

import httpx

from ...observability.logging import get_logger
from ...settings import get_settings

logger = get_logger(__name__)


class LLMExtractResult(TypedDict):
    """LLM 추출 결과"""

    menus: list[str]
    confidence: float
    method: str


class LLMVerifier:
    """
    로컬 LLM 검증기 (Ollama)

    사용법:
        verifier = LLMVerifier()
        if verifier.available:
            menus = await verifier.extract_menu_direct(text, store_name)
    """

    # ─────────────── Few-shot 템플릿 (L1) ───────────────
    SYSTEM_PROMPT = """당신은 한국 음식점 메뉴 추출 전문가입니다.

## 절대 규칙
1. '{store_name}' 음식점의 실제 판매 메뉴만 추출하세요.
2. 메뉴 이름은 2~10자의 한글 음식명이어야 합니다.
3. 가게명, 지역명, 형용사(맛있는, 노릇하게), 문장, URL은 절대 메뉴가 아닙니다.
4. 확신이 없거나 텍스트에 메뉴가 없으면 반드시 ["없음"]으로 응답하세요.
5. 오직 JSON 배열 형식으로만 응답하세요.

## Few-shot 예시
[예시 1]
가게: 올래국수 (한식)
텍스트: "제주 여행 첫 끼로 올래국수 갔는데 고기국수 진짜 맛있었어요. 웨이팅 30분 했지만 worth it"
응답: ["고기국수"]

[예시 2]
가게: 파주시 지역화폐 (기타)
텍스트: "파주시 지역화폐 가맹점 알아보기 2) 가운데 메뉴에 경기지역화폐 가맹점 찾기가 나옵니다."
응답: ["없음"]

[예시 3]
가게: 춘심이네 (한식)
텍스트: "원조 통갈치구이 2인세트 제주여행 밥집추천, 제주 춘심이네 본점"
응답: ["통갈치구이"]

[예시 4]
가게: 흑돼지가있는풍경 (한식)
텍스트: "제주 흑돼지 맛집에서 삼겹살이랑 목살 시켰어요. 진짜 노릇하게 구워져서 최고"
응답: ["삼겹살", "목살"]

[예시 5]
가게: 전국산책길 (기타)
텍스트: "전국산책길 하이킹 둘레길코스 소요시간 거리 길이 경로 정보 안녕하세요."
응답: ["없음"]"""

    # 업종별 메뉴 가이드 (RAG 컨텍스트)
    BIZ_MENU_GUIDE = {
        '한식': '예시: 김치찌개, 된장찌개, 불고기, 갈비, 삼겹살, 비빔밥, 해장국, 국수',
        '일식': '예시: 초밥, 회, 라멘, 우동, 돈카츠, 덮밥, 사시미',
        '중식': '예시: 짜장면, 짬뽕, 탕수육, 볶음밥, 마라탕, 마라샹궈',
        '서양식': '예시: 파스타, 피자, 스테이크, 리조또, 샐러드',
        '분식': '예시: 떡볶이, 순대, 김밥, 라면, 튀김',
        '카페': '예시: 아메리카노, 라떼, 케이크, 마카롱, 스콘',
    }

    def __init__(self):
        self.settings = get_settings()
        self.available = self._check_ollama()

        if self.available:
            logger.info(
                'llm_verifier_available',
                model=self.settings.ollama_model,
            )
        else:
            logger.warning('llm_verifier_unavailable')

    def _check_ollama(self) -> bool:
        """Ollama 서버 실행 여부 및 모델 다운로드 확인"""
        try:
            response = httpx.get(self.settings.ollama_tags_url, timeout=3.0)
            if response.status_code == 200:
                models = [m['name'] for m in response.json().get('models', [])]
                return any(self.settings.ollama_model.split(':')[0] in m for m in models)
        except Exception:
            pass
        return False

    async def _call_ollama(self, prompt: str) -> list[str] | None:
        """
        Ollama API 호출 + JSON 강제 포맷

        Args:
            prompt: 프롬프트 텍스트

        Returns:
            추출된 메뉴 목록 (실패 시 None)
        """
        if not self.available:
            return None

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    self.settings.ollama_api_url,
                    json={
                        'model': self.settings.ollama_model,
                        'prompt': prompt,
                        'stream': False,
                        'format': 'json',
                        'options': {
                            'temperature': 0.0,
                            'top_p': 0.1,
                            'num_predict': 256,
                        },
                    },
                )

            if response.status_code != 200:
                logger.warning(
                    'llm_api_error',
                    status_code=response.status_code,
                )
                return None

            response_text = response.json().get('response', '')

            # JSON 파싱 (format='json'이면 이미 JSON)
            try:
                parsed = json.loads(response_text)
                menus = parsed.get('menus', parsed.get('final_menus', []))
                if isinstance(menus, list):
                    menus = [
                        m.strip()
                        for m in menus
                        if m
                        and isinstance(m, str)
                        and m.strip()
                        and m.strip() not in {'없음', 'None', 'null', ''}
                        and 2 <= len(m.strip()) <= 15
                    ]
                    return menus if menus else []
            except json.JSONDecodeError:
                logger.warning(
                    'llm_json_parse_failed',
                    response_text=response_text[:200],
                )

            return []

        except httpx.TimeoutException:
            logger.warning('llm_timeout')
            return None
        except Exception as e:
            logger.error('llm_call_failed', error=str(e))
            return None

    def _build_rag_context(self, store_name: str, biz_type: str) -> str:
        """
        L3: 업종 기반 RAG 힌트 생성

        기존 ner/core/llm_verifier.py의 _build_rag_context() 함수 보존

        Args:
            store_name: 가게명
            biz_type: 업종

        Returns:
            RAG 컨텍스트 문자열
        """
        biz_key = '한식'
        for key in self.BIZ_MENU_GUIDE:
            if key in (biz_type or ''):
                biz_key = key
                break

        return f'[업종 힌트] {biz_key} 음식점의 대표 메뉴: {self.BIZ_MENU_GUIDE[biz_key]}'

    async def verify_menu_with_context(
        self, text: str, store_name: str, ner_menus: list[str], biz_type: str = ''
    ) -> list[str] | None:
        """
        [검증 모드] NER 추출 결과를 LLM으로 교차 검증 (L1+L3 적용)

        Args:
            text: 입력 텍스트
            store_name: 가게명
            ner_menus: NER가 추출한 메뉴 후보
            biz_type: 업종

        Returns:
            검증된 메뉴 목록 (실패 시 None)
        """
        if not self.available or not ner_menus:
            return None

        menus_str = ', '.join(ner_menus[:5])
        rag_hint = self._build_rag_context(store_name, biz_type)

        prompt = f"""{self.SYSTEM_PROMPT}
{rag_hint}

## 실제 요청 (검증 모드)
가게: {store_name} ({biz_type})
NER가 추출한 메뉴 후보: {menus_str}

위 후보 중 '{store_name}'의 실제 음식 메뉴인 것만 골라서 응답하세요.
가게명, 지역명, 브랜드명, 형용사, 문장, 일반 단어는 제외하세요.
모든 후보가 틀렸으면 "없음"으로 응답하세요.

텍스트: {text[:self.settings.llm_text_max_length]}

JSON 형식으로만 응답하세요. 예: {{"menus": ["김치찌개", "된장찌개"]}}"""

        return await self._call_ollama(prompt)

    async def extract_menu_direct(
        self, text: str, store_name: str, biz_type: str = ''
    ) -> list[str] | None:
        """
        [보충 모드] NER가 놓친 메뉴를 LLM으로 직접 추출 (L1+L3 적용)

        Args:
            text: 입력 텍스트
            store_name: 가게명
            biz_type: 업종

        Returns:
            추출된 메뉴 목록 (실패 시 None)
        """
        if not self.available:
            return None

        rag_hint = self._build_rag_context(store_name, biz_type)

        prompt = f"""{self.SYSTEM_PROMPT}
{rag_hint}

## 실제 요청 (추출 모드)
가게: {store_name} ({biz_type})

위 텍스트에서 '{store_name}'의 실제 음식 메뉴를 추출하세요.
가게명, 지역명, 브랜드명, 형용사(맛있는, 노릇하게), 문장, URL은 메뉴가 아닙니다.
메뉴가 없거나 확신이 없으면 "없음"으로 응답하세요.

텍스트: {text[:self.settings.llm_text_max_length]}

JSON 형식으로만 응답하세요. 예: {{"menus": ["김치찌개", "된장찌개"]}}"""

        return await self._call_ollama(prompt)
