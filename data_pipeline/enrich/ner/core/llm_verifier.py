# data_pipeline/enrich/ner/core/llm_verifier.py
"""
로컬 LLM 검증기 (Ollama)
- 의심 케이스만 LLM으로 정밀 검증
- 비용 무료, 프라이버시 보호
"""
import json
import re
from typing import List, Optional

import httpx

OLLAMA_API_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen2.5:7b"


class LLMVerifier:
    """로컬 LLM 검증기"""
    
    def __init__(self):
        self.available = self._check_ollama()
    
    def _check_ollama(self) -> bool:
        """Ollama 서버 실행 여부 확인"""
        try:
            import requests
            response = requests.get("http://localhost:11434/api/tags", timeout=3)
            if response.status_code == 200:
                models = [m['name'] for m in response.json().get('models', [])]
                return any(OLLAMA_MODEL in m for m in models)
        except Exception:
            pass
        return False
    
    async def verify_menu(self, text: str, store_name: str) -> Optional[List[str]]:
        """LLM으로 메뉴 검증"""
        if not self.available:
            return None
        
        prompt = f"""다음 텍스트에서 '{store_name}'의 메뉴를 추출하세요.

규칙:
1. '{store_name}'의 실제 메뉴만 추출하세요.
2. 지역명, 슬랭, 일반 단어는 제외하세요.
3. 메뉴가 없으면 "없음"으로 응답하세요.
4. JSON 배열로만 응답하세요.

텍스트: {text}

응답 (JSON 배열):"""
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    OLLAMA_API_URL,
                    json={
                        "model": OLLAMA_MODEL,
                        "prompt": prompt,
                        "stream": False,
                        "options": {
                            "temperature": 0.1,
                            "max_tokens": 200,
                        }
                    }
                )
                
                if response.status_code == 200:
                    result = response.json()
                    response_text = result.get('response', '')
                    
                    # JSON 배열 추출 시도
                    json_match = re.search(r'\[.*?\]', response_text)
                    if json_match:
                        try:
                            menus = json.loads(json_match.group())
                            return menus if menus else []
                        except json.JSONDecodeError:
                            pass
                    
                    # "없음" 응답 처리
                    if '없음' in response_text:
                        return []
        except Exception as e:
            print(f"⚠️ LLM 검증 실패: {e}")
        
        return None