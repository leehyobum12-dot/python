# data_pipeline/config/taxonomy/base.py
from typing import Dict, List

class TaxonomyRegistry:
    """
    모든 Taxonomy 도메인의 공통 베이스 클래스.
    O(1) 역추적 _reverse_map으로 DB 인덱싱 최적화.
    """
    def __init__(self, data: Dict[str, List[str]]):
        self._data = data
        self._reverse_map = self._build_reverse_map()

    def _build_reverse_map(self) -> Dict[str, str]:
        r_map = {}
        for main_cat, sub_cats in self._data.items():
            for sub in sub_cats:
                r_map[sub] = main_cat
        return r_map

    def get_subcategories(self, main_category: str) -> List[str]:
        return self._data.get(main_category, [])

    def validate(self, main_category: str, sub_category: str) -> bool:
        return sub_category in self._data.get(main_category, [])

    def get_main_category(self, sub_category: str) -> str:
        return self._reverse_map.get(sub_category, "UNKNOWN")

    def get_all_keywords(self) -> set:
        """모든 서브 카테고리 키워드를 Set으로 반환 (부분 매칭용)"""
        return set(self._reverse_map.keys())

    def match(self, text: str) -> List[str]:
        """텍스트에서 매칭되는 메인 카테고리 목록 반환"""
        matched = set()
        for keyword, main_cat in self._reverse_map.items():
            if keyword in text:
                matched.add(main_cat)
        return list(matched)