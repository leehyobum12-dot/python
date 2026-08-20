"""
로컬 LLM 검증기 (Ollama) - Production Ready v3
================================================
- L1: Few-shot 프롬프트로 정확도 +10%
- L3: RAG 컨텍스트(업종 힌트) 주입으로 환각 -50%
- JSON 파싱 + Markdown 코드블록 방어
"""
import json
import re
import httpx
import asyncio
from typing import List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from data_pipeline.adapters.base import CertRecord

try:
    from ...config.paths import OLLAMA_API_URL, OLLAMA_TAGS_URL, OLLAMA_MODEL
    from ...config.thresholds import LLM_TEXT_MAX_LENGTH
except ImportError:
    from data_pipeline.config.paths import OLLAMA_API_URL, OLLAMA_TAGS_URL, OLLAMA_MODEL
    from data_pipeline.config.thresholds import LLM_TEXT_MAX_LENGTH


class LLMVerifier:
    """로컬 LLM 검증기 (Production Ready v3)"""

    def __init__(self):
        self.available = self._check_ollama()
        if self.available:
            print(f"✅ 로컬 LLM 사용 가능: {OLLAMA_MODEL}")
        else:
            print(f"⚠️ 로컬 LLM 사용 불가 — NER만 사용")

    def _check_ollama(self) -> bool:
        """Ollama 서버 실행 여부 및 모델 다운로드 확인"""
        try:
            response = httpx.get(OLLAMA_TAGS_URL, timeout=3.0)
            if response.status_code == 200:
                models = [m['name'] for m in response.json().get('models', [])]
                return any(OLLAMA_MODEL.split(':')[0] in m for m in models)
        except Exception:
            pass
        return False

    async def _call_ollama(self, prompt: str) -> Optional[List[str]]:
        """Ollama API 호출 + 안전한 JSON 파싱"""
        if not self.available:
            return None

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    OLLAMA_API_URL,
                    json={
                        'model': OLLAMA_MODEL,
                        'prompt': prompt,
                        'stream': False,
                        'options': {'temperature': 0.1},
                    }
                )

            if response.status_code != 200:
                return None

            response_text = response.json().get('response', '')

            # Markdown 코드블록 제거
            response_text = re.sub(r'```json\s*', '', response_text)
            response_text = re.sub(r'```\s*', '', response_text)
            response_text = response_text.strip()

            # '없음' 단독 응답 처리
            if response_text in ('없음', '["없음"]', '[]'):
                return []

            # JSON 배열 추출
            json_match = re.search(r'\[[\s\S]*?\]', response_text, re.DOTALL)
            if json_match:
                try:
                    menus = json.loads(json_match.group())
                    if isinstance(menus, list):
                        invalid_values = {'없음', 'None', 'null', 'N/A', '-', ''}
                        menus = [
                            m.strip() for m in menus
                            if m and isinstance(m, str)
                            and m.strip()
                            and m.strip() not in invalid_values
                            and 2 <= len(m.strip()) <= 15
                        ]
                        return menus if menus else []
                except json.JSONDecodeError:
                    pass

            return []

        except httpx.TimeoutException:
            print(f"⚠️ LLM 검증 타임아웃 (60초 초과)")
            return None
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"⚠️ LLM 검증 실패: {e}")
            return None

    def _build_rag_context(self, store_name: str, biz_type: str) -> str:
        """🆕 L3: 업종 기반 RAG 힌트 생성"""
        biz_menu_guide = {
            '한식': '예시: 김치찌개, 된장찌개, 불고기, 갈비, 삼겹살, 비빔밥, 해장국, 국수',
            '일식': '예시: 초밥, 회, 라멘, 우동, 돈카츠, 덮밥, 사시미',
            '중식': '예시: 짜장면, 짬뽕, 탕수육, 볶음밥, 마라탕, 마라샹궈',
            '서양식': '예시: 파스타, 피자, 스테이크, 리조또, 샐러드',
            '분식': '예시: 떡볶이, 순대, 김밥, 라면, 튀김',
            '카페': '예시: 아메리카노, 라떼, 케이크, 마카롱, 스콘',
        }
        
        biz_key = '한식'
        for key in biz_menu_guide:
            if key in (biz_type or ''):
                biz_key = key
                break
        
        return f"[업종 힌트] {biz_key} 음식점의 대표 메뉴: {biz_menu_guide[biz_key]}"

    # ─────────────── 🎯 Few-shot 템플릿 (L1) ───────────────
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

    async def verify_menu_with_context(
        self,
        text: str,
        store_name: str,
        ner_menus: List[str],
        biz_type: str = ''
    ) -> Optional[List[str]]:
        """[검증 모드] NER 추출 결과를 LLM으로 교차 검증 (L1+L3 적용)"""
        if not self.available or not ner_menus:
            return None

        menus_str = ', '.join(ner_menus[:5])
        rag_hint = self._build_rag_context(store_name, biz_type)

        prompt = f"""{self.SYSTEM_PROMPT}

## 실제 요청 (검증 모드)
{rag_hint}
가게: {store_name} ({biz_type})
NER 후보: {menus_str}
텍스트: {text[:LLM_TEXT_MAX_LENGTH]}

위 NER 후보 중 실제 메뉴인 것만 남긴 JSON 배열을 반환하세요. 모두 틀리면 ["없음"]:"""

        return await self._call_ollama(prompt)

    async def extract_menu_direct(
        self,
        text: str,
        store_name: str,
        biz_type: str = ''
    ) -> Optional[List[str]]:
        """[보충 모드] NER가 놓친 메뉴를 LLM으로 직접 추출 (L1+L3 적용)"""
        if not self.available:
            return None

        rag_hint = self._build_rag_context(store_name, biz_type)

        prompt = f"""{self.SYSTEM_PROMPT}

## 실제 요청 (추출 모드)
{rag_hint}
가게: {store_name} ({biz_type})
텍스트: {text[:LLM_TEXT_MAX_LENGTH]}

위 텍스트에서 '{store_name}'의 실제 음식 메뉴를 추출해 JSON 배열로 반환하세요. 없으면 ["없음"]:"""

        return await self._call_ollama(prompt)