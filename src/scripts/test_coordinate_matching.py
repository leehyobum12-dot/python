#!/usr/bin/env python3
"""
좌표 기반 매칭 진단 테스트
============================
TouristInfo 레코드와 Primary 소스 간의 거리 분포를 분석하여
최적 매칭 반경과 매칭 가능성을 진단합니다.

사용법:
    python scripts/test_coordinate_matching.py
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from math import radians, cos, sin, asin, sqrt
from typing import List, Dict, Tuple

# 경로 설정
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
SRC_DIR = os.path.join(ROOT_DIR, 'src')

if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from dotenv import load_dotenv
load_dotenv()

from bareun_pipeline.settings import get_settings
from bareun_pipeline.domain.record import CertRecord


def haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """두 좌표 사이 거리 (미터)"""
    R = 6371000
    lat1, lng1, lat2, lng2 = map(radians, [lat1, lng1, lat2, lng2])
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlng / 2) ** 2
    return R * 2 * asin(sqrt(a))


def load_latest_pre_merge() -> List[CertRecord]:
    """최신 pre_merge NDJSON 로드"""
    settings = get_settings()
    
    if not os.path.exists(settings.silver_dir):
        raise FileNotFoundError(f'Silver 디렉토리 없음: {settings.silver_dir}')
    
    files = sorted([
        f for f in os.listdir(settings.silver_dir)
        if f.startswith('pre_merge_') and f.endswith('.ndjson')
    ], reverse=True)
    
    if not files:
        raise FileNotFoundError('pre_merge 파일 없음. 먼저 E2E 실행하세요.')
    
    path = os.path.join(settings.silver_dir, files[0])
    print(f'📂 로드: {path}')
    
    records = []
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(CertRecord.model_validate(json.loads(line)))
            except Exception as e:
                print(f'   ⚠️ 로드 실패: {str(e)[:100]}')
    
    print(f'✅ 로드 완료: {len(records)}건\n')
    return records


def analyze_matching(records: List[CertRecord]) -> None:
    """매칭 가능성 분석"""
    certified_sources = {'safe_restaurant', 'low_sodium', 'model_restaurant', 'food_safety'}
    
    # 소스 분리
    tourist = [r for r in records if r.source == 'tourist_info' and r.has_coordinate()]
    general = [r for r in records if r.source == 'general_restaurant' and r.has_coordinate()]
    primary = [r for r in records if r.source in certified_sources and r.has_coordinate()]
    
    print('=' * 70)
    print('📊 데이터 현황')
    print('=' * 70)
    print(f'  TouristInfo (좌표 있음): {len(tourist)}건')
    print(f'  GeneralRestaurant (좌표 있음): {len(general)}건')
    print(f'  Primary 소스 (좌표 있음): {len(primary)}건')
    
    if not tourist or not primary:
        print('\n❌ 매칭 분석 불가 (TouristInfo 또는 Primary 소스 없음)')
        return
    
    print(f'\n🔍 매칭 대상: TouristInfo {len(tourist)}건 vs Primary {len(primary)}건')
    print(f'   총 비교 횟수: {len(tourist) * len(primary):,}회\n')
    
    # 각 TouristInfo마다 가장 가까운 Primary 찾기
    results: List[Dict] = []
    
    for t in tourist:
        distances = []
        for p in primary:
            dist = haversine(t.lat, t.lng, p.lat, p.lng)
            distances.append((dist, p))
        
        distances.sort(key=lambda x: x[0])
        min_dist, closest_primary = distances[0]
        
        # 두 번째로 가까운 것도 추적
        second_dist = distances[1][0] if len(distances) > 1 else None
        
        results.append({
            'tourist': t,
            'closest_primary': closest_primary,
            'min_distance': min_dist,
            'second_distance': second_dist,
            'top5': distances[:5],
        })
    
    # 거리 기준 정렬
    results.sort(key=lambda x: x['min_distance'])
    
    # 구간별 분포
    buckets = {
        '0~100m': 0,
        '100~300m': 0,
        '300~500m': 0,
        '500m~1km': 0,
        '1~5km': 0,
        '5~10km': 0,
        '10km+': 0,
    }
    
    for r in results:
        d = r['min_distance']
        if d < 100:
            buckets['0~100m'] += 1
        elif d < 300:
            buckets['100~300m'] += 1
        elif d < 500:
            buckets['300~500m'] += 1
        elif d < 1000:
            buckets['500m~1km'] += 1
        elif d < 5000:
            buckets['1~5km'] += 1
        elif d < 10000:
            buckets['5~10km'] += 1
        else:
            buckets['10km+'] += 1
    
    # 거리 분포 출력
    print('=' * 70)
    print('📏 거리 분포 (TouristInfo → 가장 가까운 Primary)')
    print('=' * 70)
    
    all_distances = [r['min_distance'] for r in results]
    print(f'  최소: {min(all_distances):.1f}m')
    print(f'  최대: {max(all_distances):.1f}m ({max(all_distances)/1000:.1f}km)')
    print(f'  평균: {sum(all_distances)/len(all_distances):.1f}m')
    
    sorted_d = sorted(all_distances)
    n = len(sorted_d)
    print(f'  P25: {sorted_d[int(n*0.25)]:.1f}m')
    print(f'  P50 (중앙값): {sorted_d[int(n*0.5)]:.1f}m')
    print(f'  P75: {sorted_d[int(n*0.75)]:.1f}m')
    print(f'  P90: {sorted_d[int(n*0.9)]:.1f}m')
    
    print('\n📊 구간별 분포:')
    cumulative = 0
    for bucket, count in buckets.items():
        cumulative += count
        pct = count / len(results) * 100
        cum_pct = cumulative / len(results) * 100
        bar = '█' * int(pct / 2)
        print(f'  {bucket:>10}: {count:>3}건 ({pct:>5.1f}%) [누적 {cum_pct:>5.1f}%] {bar}')
    
    # 반경별 매칭 가능 건수
    print('\n' + '=' * 70)
    print('🎯 반경별 매칭 가능 건수')
    print('=' * 70)
    for radius in [50, 100, 200, 300, 500, 1000, 2000, 5000]:
        matched = sum(1 for r in results if r['min_distance'] <= radius)
        pct = matched / len(results) * 100
        print(f'  {radius:>5}m: {matched:>3}/{len(results)}건 ({pct:>5.1f}%)')
    
    # 상위 매칭 후보 상세 출력
    print('\n' + '=' * 70)
    print('🔍 매칭 후보 상세 (거리순 상위 10개)')
    print('=' * 70)
    print(f'{"#":>2} | {"TouristInfo 이름":<20} | {"Primary 이름":<20} | {"거리":>8} | {"Primary 소스"}')
    print('-' * 95)
    
    for i, r in enumerate(results[:10], 1):
        t_name = r['tourist'].raw_name[:18] if r['tourist'].raw_name else '(없음)'
        p_name = r['closest_primary'].raw_name[:18] if r['closest_primary'].raw_name else '(없음)'
        dist = r['min_distance']
        source = r['closest_primary'].source
        
        dist_str = f'{dist:.0f}m' if dist < 1000 else f'{dist/1000:.1f}km'
        
        # 같은 이름이면 강조
        name_match = (
            t_name and p_name and 
            (t_name in p_name or p_name in t_name or 
             any(c in p_name for c in t_name[:3]))
        )
        marker = '✨' if name_match else '  '
        
        print(f'{i:>2} | {t_name:<20} | {p_name:<20} | {dist_str:>8} | {source:<18} {marker}')
    
    # 하위 매칭 후보 (거리 먼 것)
    print('\n' + '=' * 70)
    print('⚠️ 매칭 불가능 후보 (거리순 하위 5개)')
    print('=' * 70)
    print(f'{"#":>2} | {"TouristInfo 이름":<20} | {"Primary 이름":<20} | {"거리":>8} | {"Primary 소스"}')
    print('-' * 95)
    
    for i, r in enumerate(results[-5:], len(results) - 4):
        t_name = r['tourist'].raw_name[:18] if r['tourist'].raw_name else '(없음)'
        p_name = r['closest_primary'].raw_name[:18] if r['closest_primary'].raw_name else '(없음)'
        dist = r['min_distance']
        source = r['closest_primary'].source
        
        dist_str = f'{dist:.0f}m' if dist < 1000 else f'{dist/1000:.1f}km'
        print(f'{i:>2} | {t_name:<20} | {p_name:<20} | {dist_str:>8} | {source:<18}')
    
    # 결론 도출
    print('\n' + '=' * 70)
    print('💡 진단 결론')
    print('=' * 70)
    
    matched_500 = sum(1 for r in results if r['min_distance'] <= 500)
    matched_100 = sum(1 for r in results if r['min_distance'] <= 100)
    median = sorted_d[n // 2]
    
    print(f'\n  현재 반경 (100m): {matched_100}/{len(results)}건 매칭 가능 ({matched_100/len(results)*100:.1f}%)')
    print(f'  반경 500m 확대: {matched_500}/{len(results)}건 매칭 가능 ({matched_500/len(results)*100:.1f}%)')
    print(f'  중앙값 거리: {median:.0f}m')
    
    if matched_100 == 0 and median > 1000:
        print('\n  ❌ 결론: TouristInfo 19건은 실제로 다른 식당일 가능성이 높음')
        print('     → 좌표 기반 매칭으로는 해결 불가')
        print('     → 상호명 fuzzy matching 또는 별도 독립 데이터로 취급 필요')
    elif matched_500 > len(results) * 0.3:
        print('\n  ✅ 결론: 반경을 300~500m로 확대하면 상당수 매칭 가능')
        print('     → Merger의 matching_radius_m 파라미터 조정 권장')
    else:
        print('\n  ⚠️ 결론: 일부만 매칭 가능')
        print('     → 상호명 유사도 기반 매칭 로직 추가 고려')


def main():
    print('=' * 70)
    print('🧪 좌표 기반 매칭 진단 테스트')
    print('=' * 70)
    print()
    
    try:
        records = load_latest_pre_merge()
        analyze_matching(records)
    except Exception as e:
        print(f'\n❌ 에러: {e}')
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    main()