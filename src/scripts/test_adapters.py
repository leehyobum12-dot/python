#!/usr/bin/env python3
"""
개별 어댑터 단위 테스트
======================
새로 추가된 어댑터(TouristInfo, GeneralRestaurant)만 격리 테스트

사용법:
    python scripts/test_adapters.py --adapter tourist
    python scripts/test_adapters.py --adapter general
    python scripts/test_adapters.py --adapter both
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from typing import List

# 경로 설정
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
SRC_DIR = os.path.join(ROOT_DIR, 'src')

if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from dotenv import load_dotenv
load_dotenv()

from bareun_pipeline.settings import get_settings
from bareun_pipeline.observability.logging import setup_logging, get_logger
from bareun_pipeline.domain.record import CertRecord

logger = get_logger(__name__)


def test_tourist_info(province: str = '제주특별자치도', max_details: int = 5):
    """TouristInfoAdapter 단위 테스트"""
    print('\n' + '=' * 70)
    print(f'🧪 TouristInfoAdapter 테스트: {province}')
    print(f'   상세 조회: {max_details}건 (일일 트래픽 보호)')
    print('=' * 70)

    from bareun_pipeline.adapters.tourist_info import TouristInfoAdapter

    start = time.time()
    adapter = TouristInfoAdapter(
        fetch_details=True,
        max_detail_count=max_details,
    )

    print(f'\n🔑 API 키 설정: {"✅ 있음" if adapter.service_key else "❌ 없음"}')
    print(f'📍 지역 코드: {adapter.AREA_CODE_MAP.get(province)} ({"✅ 유효" if adapter.AREA_CODE_MAP.get(province) else "❌ 없음"})')

    if not adapter.service_key:
        print('\n❌ DATA_GO_KR_KEY가 .env에 없습니다.')
        return

    print('\n📥 데이터 수집 시작...')
    records = adapter.collect(province=province)

    elapsed = time.time() - start
    print(f'\n⏱️  소요 시간: {elapsed:.1f}초')
    print(f'📊 수집 건수: {len(records)}건')
    print(f'📊 API 호출: {adapter._detail_count}건')

    if not records:
        print('\n❌ 수집된 레코드 없음. API 응답 구조 확인 필요.')
        print('   tourist_info.py의 _fetch_list_page() 디버깅 로그 확인')
        return

    # 결과 분석
    print('\n📊 데이터 분석:')
    
    with_menu = sum(1 for r in records if r.main_menu)
    with_photos = sum(1 for r in records if r.raw and isinstance(r.raw, dict) 
                      and r.raw.get('external_info', {}).get('photos'))
    with_hours = sum(1 for r in records if r.raw and isinstance(r.raw, dict) 
                     and r.raw.get('external_info', {}).get('business_hours'))
    with_coords = sum(1 for r in records if r.has_coordinate())

    print(f'   메뉴 있음: {with_menu}건')
    print(f'   사진 있음: {with_photos}건')
    print(f'   영업시간 있음: {with_hours}건')
    print(f'   좌표 있음: {with_coords}건')

    # 샘플 출력
    print('\n📋 샘플 레코드 (첫 3건):')
    for i, r in enumerate(records[:3]):
        print(f'\n   [{i+1}] {r.raw_name}')
        print(f'       주소: {r.raw_address}')
        print(f'       좌표: {r.lat}, {r.lng}')
        print(f'       메인메뉴: {r.main_menu or "(없음)"}')
        print(f'       메뉴들: {r.menus[:3] if r.menus else []}')
        if r.raw and isinstance(r.raw, dict):
            ext = r.raw.get('external_info', {})
            photos = ext.get('photos', [])
            hours = ext.get('business_hours', {})
            print(f'       사진: {len(photos)}개')
            if photos:
                print(f'           첫 사진: {photos[0].get("url", "")[:60]}...')
            print(f'       영업시간: {hours}')

    # 결과 파일로 저장
    result_path = os.path.join(
        get_settings().data_dir,
        'test_results',
        f'tourist_info_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
    )
    os.makedirs(os.path.dirname(result_path), exist_ok=True)
    
    result_data = {
        'province': province,
        'timestamp': datetime.now().isoformat(),
        'elapsed_seconds': round(elapsed, 1),
        'total_count': len(records),
        'api_calls': adapter._detail_count,
        'stats': {
            'with_menu': with_menu,
            'with_photos': with_photos,
            'with_hours': with_hours,
            'with_coords': with_coords,
        },
        'sample': [
            {
                'name': r.raw_name,
                'address': r.raw_address,
                'lat': r.lat,
                'lng': r.lng,
                'main_menu': r.main_menu,
                'menus': r.menus[:5] if r.menus else [],
            }
            for r in records[:5]
        ]
    }
    
    with open(result_path, 'w', encoding='utf-8') as f:
        json.dump(result_data, f, ensure_ascii=False, indent=2)
    print(f'\n💾 결과 저장: {result_path}')


def test_general_restaurant(province: str = '제주특별자치도', max_pages: int = 2):
    """GeneralRestaurantAdapter 단위 테스트"""
    print('\n' + '=' * 70)
    print(f'🧪 GeneralRestaurantAdapter 테스트: {province}')
    print(f'   최대 페이지: {max_pages} ({max_pages * 500}건)')
    print('=' * 70)

    from bareun_pipeline.adapters.general_restaurant import GeneralRestaurantAdapter

    start = time.time()
    adapter = GeneralRestaurantAdapter(max_pages=max_pages)

    print(f'\n🔑 API 키 설정: {"✅ 있음" if adapter.service_key else "❌ 없음"}')
    print(f'📍 최대 페이지: {adapter.max_pages}')

    if not adapter.service_key:
        print('\n❌ DATA_GO_KR_KEY가 .env에 없습니다.')
        return

    print('\n📥 데이터 수집 시작...')
    records = adapter.collect(province=province)

    elapsed = time.time() - start
    print(f'\n⏱️  소요 시간: {elapsed:.1f}초')
    print(f'📊 수집 건수: {len(records)}건')

    if not records:
        print('\n❌ 수집된 레코드 없음.')
        print('   API 응답 구조 또는 파라미터 확인 필요.')
        return

    # 결과 분석
     # 결과 분석 부분 수정
    print('\n📊 데이터 분석:')
    
    with_grade = sum(1 for r in records if len(r.certs) > 1)
    with_coords = sum(1 for r in records if r.has_coordinate())
    with_phone = sum(1 for r in records if r.phone)

    # 위생등급 통계
    grade_counts = {}
    for r in records:
        for cert in r.certs:
            if cert.startswith('hygiene_grade_'):
                grade = cert.replace('hygiene_grade_', '')
                grade_counts[grade] = grade_counts.get(grade, 0) + 1

    print(f'   위생등급 있음: {with_grade}건')
    print(f'   좌표 있음: {with_coords}건')
    print(f'   전화번호 있음: {with_phone}건')
    if grade_counts:
        print(f'   등급별 분포: {grade_counts}')

    # 샘플 출력
    print('\n📋 샘플 레코드 (첫 3건):')
    for i, r in enumerate(records[:3]):
        print(f'\n   [{i+1}] {r.raw_name}')
        print(f'       주소: {r.raw_address}')
        print(f'       좌표: {r.lat}, {r.lng}')
        print(f'       업종: {r.biz_type}')
        print(f'       전화번호: {r.phone or "(없음)"}')
        print(f'       certs: {r.certs}')
        print(f'       지정일: {r.designated_at}')

    # pyproj 체크
    print('\n🔍 pyproj 설치 상태 확인:')
    try:
        import pyproj
        print(f'   ✅ pyproj 설치됨 (버전: {pyproj.__version__})')
    except ImportError:
        print('   ❌ pyproj 미설치 (좌표 변환 근사치 사용)')
        print('      설치 명령: pip install pyproj')

    # 결과 파일로 저장
    result_path = os.path.join(
        get_settings().data_dir,
        'test_results',
        f'general_restaurant_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
    )
    os.makedirs(os.path.dirname(result_path), exist_ok=True)
    
    result_data = {
        'province': province,
        'timestamp': datetime.now().isoformat(),
        'elapsed_seconds': round(elapsed, 1),
        'total_count': len(records),
        'max_pages': max_pages,
        'stats': {
            'with_grade': with_grade,
            'with_coords': with_coords,
            'with_phone': with_phone,
            'grade_distribution': grade_counts,
        },
        'sample': [
            {
                'name': r.raw_name,
                'address': r.raw_address,
                'lat': r.lat,
                'lng': r.lng,
                'biz_type': r.biz_type,
                'certs': r.certs,
            }
            for r in records[:5]
        ]
    }
    
    with open(result_path, 'w', encoding='utf-8') as f:
        json.dump(result_data, f, ensure_ascii=False, indent=2)
    print(f'\n💾 결과 저장: {result_path}')


def main():
    parser = argparse.ArgumentParser(description='개별 어댑터 단위 테스트')
    parser.add_argument(
        '--adapter',
        choices=['tourist', 'general', 'both'],
        default='both',
        help='테스트할 어댑터 (기본: both)'
    )
    parser.add_argument('--province', default='제주특별자치도', help='시도명')
    parser.add_argument(
        '--max-details',
        type=int,
        default=5,
        help='TouristInfo 상세 조회 건수 (일일 트래픽 보호)'
    )
    parser.add_argument(
        '--max-pages',
        type=int,
        default=2,
        help='GeneralRestaurant 최대 페이지'
    )
    args = parser.parse_args()

    setup_logging(level=20, json_output=False)

    print('=' * 70)
    print('🔬 개별 어댑터 단위 테스트')
    print('=' * 70)

    if args.adapter in ('tourist', 'both'):
        test_tourist_info(args.province, args.max_details)

    if args.adapter in ('general', 'both'):
        test_general_restaurant(args.province, args.max_pages)

    print('\n' + '=' * 70)
    print('✅ 테스트 완료')
    print('=' * 70)


if __name__ == '__main__':
    main()