from .base import TaxonomyRegistry

OCCASION_DATA = {
    '뷰': ['오션뷰', '한옥', '야경', '루프탑'],
    '프라이빗': ['독실', '룸', '예약제'],
    '코스·파인': ['코스', '파인다이닝', '정식'],
    '이벤트': ['프로포즈', '상견례', '돌잔치', '회갑'],
    '단체': ['뷔페', '단체석'],
}
OccasionTaxonomy = TaxonomyRegistry(OCCASION_DATA)