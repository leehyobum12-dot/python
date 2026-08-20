from .base import TaxonomyRegistry

NUTRIENT_DATA = {
    '탄수화물': ['죽', '밥', '면', '떡', '국수', '칼국수'],
    '단백질': ['돼지', '소', '닭', '양', '수산물', '두부', '계란'],
    '지방': ['튀김', '삼겹살', '갈비', '치즈', '버터'],
    '식이섬유': ['채소', '해조류', '나물', '샐러드'],
}
NutrientTaxonomy = TaxonomyRegistry(NUTRIENT_DATA)