"""
pytest fixtures
================
모든 테스트에서 공유하는 fixtures를 정의합니다.
"""
from __future__ import annotations

import os
import sys
import pytest

# src 디렉토리를 Python 경로에 추가
SRC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'src'
)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)


@pytest.fixture
def sample_cert_record():
    """샘플 CertRecord 생성"""
    from bareun_pipeline.domain.record import CertRecord

    return CertRecord(
        source='safe_restaurant',
        cert_type='safe_restaurant',
        raw_name='올래국수',
        raw_address='제주특별자치도 제주시 중앙로 1길 10',
        province='제주특별자치도',
        municipality='제주시',
        emd='일도일동',
        phone='0641234567',
        biz_type='한식',
        main_menu='고기국수',
        main_menu_source='public_api',
        lat=33.4996,
        lng=126.5312,
        is_active=True,
    )


@pytest.fixture
def sample_cert_records():
    """샘플 CertRecord 목록 생성 (병합 테스트용)"""
    from bareun_pipeline.domain.record import CertRecord

    return [
        CertRecord(
            source='safe_restaurant',
            cert_type='safe_restaurant',
            raw_name='올래국수',
            raw_address='제주특별자치도 제주시 중앙로 1길 10',
            province='제주특별자치도',
            municipality='제주시',
            phone='0641234567',
            biz_type='한식',
            main_menu='고기국수',
            main_menu_source='public_api',
            lat=33.4996,
            lng=126.5312,
            is_active=True,
        ),
        CertRecord(
            source='food_safety',
            cert_type='food_safety',
            raw_name='올래국수',
            raw_address='제주특별자치도 제주시 중앙로 1길 10',
            province='제주특별자치도',
            municipality='제주시',
            phone='0641234567',
            biz_type='한식',
            main_menu='고기국수',
            main_menu_source='public_api',
            lat=33.4996,
            lng=126.5312,
            is_active=True,
        ),
        CertRecord(
            source='low_sodium',
            cert_type='low_sodium',
            raw_name='우진해장국',
            raw_address='제주특별자치도 제주시 서광로 2길 5',
            province='제주특별자치도',
            municipality='제주시',
            phone='0649876543',
            biz_type='한식',
            main_menu='몸국',
            main_menu_source='public_api',
            lat=33.5001,
            lng=126.5305,
            is_active=True,
        ),
    ]


@pytest.fixture
def sample_gold_restaurant():
    """샘플 GoldRestaurant 생성"""
    from bareun_pipeline.gold.transform import GoldRestaurant

    return GoldRestaurant(
        dedup_key='test_dedup_key_1234567890abcdef',
        data_source='public_api',
        store_name='올래국수',
        province='제주특별자치도',
        municipality='제주시',
        emd='일도일동',
        address_road='중앙로 1길 10',
        address_lotnum=None,
        lat=33.4996,
        lng=126.5312,
        main_menu='고기국수',
        main_menu_source='public_api',
        menus=['몸국', '비빔밥'],
        menus_source='public_api',
        phone='0641234567',
        biz_type='한식',
        certs=['safe_restaurant'],
        cuisine_tags=['한식'],
        nutrient_tags=['탄수화물'],
        cooking_tags=['국물'],
        ingredient_tags=[],
        regional_tags=['제주'],
        allergen_tags=[],
        occasion_tags=[],
        avoid_tags=[],
        quality_score=85,
    )