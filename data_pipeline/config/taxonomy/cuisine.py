from .base import TaxonomyRegistry

CUISINE_DATA = {
    '한식': ['일반', '분식', '궁중', '향토', '사찰'],
    '중식': ['짜장', '짬뽕', '볶음밥', '탕수육'],
    '양식': ['스테이크', '파스타', '피자', '버거', '샐러드'],
    '일식': ['초밥', '회', '라멘', '우동', '돈카츠'],
    '디저트': ['카페', '베이커리', '아이스크림', '전통다과'],
}
CuisineTaxonomy = TaxonomyRegistry(CUISINE_DATA)