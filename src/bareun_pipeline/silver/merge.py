"""
Silver Layer: 단일 병합 로직 (SSOT)
=====================================
- dedup_key 기반 병합 (한 번만 수행)
- 멀티 키 스코어 매칭 (이름 + 주소 + 좌표)
- numpy 벡터화 + ThreadPoolExecutor 병렬 처리 (성능 최적화)
- 인증 타입 통합
- 7차원 태그 배열 통합
- 메뉴/주소/좌표 우선순위 병합
- avoid_tags / store_name_quality / quality_score 병합
- TouristInfo 독립 배지 유지
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from math import asin, cos, radians, sin, sqrt
from typing import Any

from ..domain.dedup import generate_dedup_key
from ..domain.record import CertRecord
from ..observability.logging import get_logger

# 🆕 numpy 안정적 import
try:
    import numpy

    np: Any | None = numpy  # 타입 힌트로 Optional[Any] 지정
    HAS_NUMPY = True
except ImportError:
    np = None
    HAS_NUMPY = False

logger = get_logger(__name__)

if not HAS_NUMPY:
    logger.warning('numpy_not_available_fallback_to_pure_python')


def haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """두 좌표 사이 거리 (미터)"""
    R = 6371000
    lat1, lng1, lat2, lng2 = map(radians, [lat1, lng1, lat2, lng2])
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlng / 2) ** 2
    return R * 2 * asin(sqrt(a))


class Merger:
    """
    레코드 병합기 (SSOT)

    사용법:
        merger = Merger()
        merged = merger.merge(records)
    """

    # 메뉴 출처 우선순위 (높을수록 우선)
    MENU_SOURCE_PRIORITY: dict[str, int] = {
        'public_api': 4,
        'cross_source': 3,
        'llm_verified': 3,
        'tourist_info': 3,
        'naver_api': 2,
        'kakao_api': 2,
        'naver_blog': 2,
        'naver_cafe': 2,
        'llm_filled': 2,
        'naver_news': 1,
        'original': 1,
    }

    # store_name_quality 우선순위 (높을수록 우선)
    QUALITY_PRIORITY: dict[str, int] = {
        'verified': 3,
        'valid': 2,
        'needs_verify': 1,
    }

    def __init__(self, matching_radius_m: int = 500):
        """
        Args:
            matching_radius_m: 좌표 기반 매칭 반경 (미터)
                              기본값 500m (좌표계 변환 오류 + 복합상가 포함)
        """
        from .normalize import Normalizer

        self._normalizer = Normalizer()
        self.matching_radius_m = matching_radius_m

    def _normalize_name(self, name: str) -> str:
        """이름 정규화 (비교용)"""
        if not name:
            return ''
        name = re.sub(r'[^가-힣a-zA-Z0-9]', '', name)
        return name.lower()

    def _name_similarity(self, name1: str, name2: str) -> float:
        """이름 유사도 계산 (0~1)"""
        n1 = self._normalize_name(name1)
        n2 = self._normalize_name(name2)

        if not n1 or not n2:
            return 0.0

        if n1 in n2 or n2 in n1:
            return 0.9

        return SequenceMatcher(None, n1, n2).ratio()

    def _address_similarity(self, addr1: str, addr2: str) -> float:
        """주소 유사도 계산 (0~1)

        버그 수정: 한쪽이 다른 쪽에 포함되는 경우에도
        길이 비율을 고려하여 과도한 점수 방지

        예시:
            "제주특별자치도 서귀포시" vs "제주특별자치도 서귀포시 하예하동로16번길 11-1"
            → len_ratio = 10/23 = 0.43
            → 점수 = 0.95 * 0.43 = 0.41 (과도한 점수 방지)
        """
        if not addr1 or not addr2:
            return 0.0

        a1 = re.sub(r'[^가-힣0-9]', '', addr1)
        a2 = re.sub(r'[^가-힣0-9]', '', addr2)

        if not a1 or not a2:
            return 0.0

        # 🆕 길이 비율 체크 (한쪽이 2배 이상 길면 포함 관계여도 감점)
        len_ratio = min(len(a1), len(a2)) / max(len(a1), len(a2))

        if a1 in a2 or a2 in a1:
            # 짧은 주소가 긴 주소의 일부일 때, 길이 비율만큼 감점
            return 0.95 * len_ratio

        return SequenceMatcher(None, a1, a2).ratio()

    def _coordinate_proximity(self, dist_m: float) -> float:
        """좌표 근접도 계산 (0~1)"""
        if dist_m <= 0:
            return 1.0
        if dist_m >= self.matching_radius_m:
            return 0.0
        return 1.0 - (dist_m / self.matching_radius_m)

    def _merge_by_fuzzy_matching(self, records: list[CertRecord]) -> list[CertRecord]:
        """
        멀티 키 스코어 기반 매칭 (성능 최적화 버전)

        최적화 전략:
        1. numpy 벡터화: Primary 좌표 배열로 거리 계산 O(N) → O(1)
        2. ThreadPoolExecutor: TouristInfo 50개 이상 시 병렬 처리
        3. 사전 필터링: 이름+주소 유사도 30% 미만 스킵
        4. 500m 이내 후보만 상세 비교

        스코어 = 이름 유사도 × 0.5 + 주소 유사도 × 0.3 + 좌표 근접도 × 0.2
        """
        certified_sources = {'safe_restaurant', 'low_sodium', 'model_restaurant', 'food_safety'}

        tourist = [r for r in records if r.source == 'tourist_info']
        primary = [r for r in records if r.source in certified_sources]
        general = [r for r in records if r.source == 'general_restaurant']

        if not tourist or not primary:
            logger.warning(
                'fuzzy_matching_skip',
                tourist_count=len(tourist),
                primary_count=len(primary),
            )
            return records

        # 🆕 numpy가 있으면 벡터화로 거리 계산 최적화
        use_numpy = HAS_NUMPY and np is not None and len(primary) > 100
        use_parallel = len(tourist) > 50

        if use_numpy:
            # Primary 좌표 배열 생성 (numpy)
            primary_coords = np.array(
                [
                    [p.lat, p.lng] if p.has_coordinate() else [float('nan'), float('nan')]
                    for p in primary
                ]
            )
            logger.info(
                'fuzzy_matching_optimization_enabled',
                numpy=True,
                parallel=use_parallel,
                primary_count=len(primary),
                tourist_count=len(tourist),
            )
        else:
            primary_coords = None

        matched_count = 0
        high_confidence = 0
        medium_confidence = 0
        still_unmatched = []

        # 디버깅 통계
        filtered_by_low_similarity = 0
        filtered_by_low_score = 0
        sample_debug_logs = []

        def match_one_tourist(t: CertRecord):
            """단일 TouristInfo 매칭 (벡터화 지원)"""
            if not t.has_coordinate():
                return None, 0.0, 0.0, 0.0, float('inf')

            best_match = None
            best_score = 0.0
            best_dist = float('inf')
            best_name_sim = 0.0
            best_addr_sim = 0.0

            # 🆕 numpy 타입 체크 강화
            if use_numpy and np is not None and primary_coords is not None:
                t_coord = np.array([t.lat, t.lng])
                diffs = primary_coords - t_coord

                # Haversine 근사 (위도 1도 ≈ 111km, 경도 1도 ≈ 111km * cos(lat))
                lat_factor = 111000
                lng_factor = 111000 * float(np.cos(np.radians(t.lat)))
                distances = np.sqrt(
                    (diffs[:, 0] * lat_factor) ** 2 + (diffs[:, 1] * lng_factor) ** 2
                )

                # 500m 이내만 필터링
                valid_mask = distances < self.matching_radius_m
                if not np.any(valid_mask):
                    return None, 0.0, 0.0, 0.0, float('inf')

                candidate_indices = np.where(valid_mask)[0]
            else:
                # numpy 없으면 기존 방식
                candidate_indices = range(len(primary))
                distances = None

            for idx in candidate_indices:
                p = primary[idx]

                # 거리 계산 (numpy가 있으면 이미 계산됨)
                if distances is not None:
                    dist = float(distances[idx])
                elif p.has_coordinate():
                    dist = haversine(t.lat, t.lng, p.lat, p.lng)
                else:
                    dist = float('inf')

                name_sim = self._name_similarity(t.raw_name, p.raw_name)
                addr_sim = self._address_similarity(t.raw_address, p.raw_address)

                # 사전 필터링
                if name_sim < 0.3 and addr_sim < 0.3:
                    continue

                coord_prox = self._coordinate_proximity(dist)
                score = name_sim * 0.5 + addr_sim * 0.3 + coord_prox * 0.2

                if score > best_score:
                    best_score = score
                    best_match = p
                    best_dist = dist
                    best_name_sim = name_sim
                    best_addr_sim = addr_sim

            return best_match, best_score, best_name_sim, best_addr_sim, best_dist

        # 🆕 병렬 처리 (TouristInfo가 50개 이상이면)
        if use_parallel:
            with ThreadPoolExecutor(max_workers=8) as executor:
                results = list(executor.map(match_one_tourist, tourist))
        else:
            results = [match_one_tourist(t) for t in tourist]

        for t, (best_match, best_score, best_name_sim, best_addr_sim, best_dist) in zip(
            tourist, results, strict=False
        ):
            if best_match and best_score >= 0.6:
                self._merge_tourist_info(best_match, t)
                matched_count += 1

                if best_score >= 0.8:
                    high_confidence += 1
                else:
                    medium_confidence += 1

                logger.info(
                    'fuzzy_match_found',
                    tourist=t.raw_name,
                    primary=best_match.raw_name,
                    score=round(best_score, 3),
                    name_sim=round(best_name_sim, 3),
                    addr_sim=round(best_addr_sim, 3),
                    distance_m=round(best_dist, 1),
                )
            else:
                still_unmatched.append(t)
                filtered_by_low_score += 1

                # 디버깅용 상위 5개 실패 케이스 보관
                if len(sample_debug_logs) < 5:
                    sample_debug_logs.append(
                        {
                            'tourist': t.raw_name,
                            'tourist_addr': (t.raw_address or '')[:40],
                            'best_primary': best_match.raw_name if best_match else '(없음)',
                            'best_primary_addr': (best_match.raw_address or '')[:40]
                            if best_match
                            else '',
                            'best_score': round(best_score, 3),
                            'best_name_sim': round(best_name_sim, 3),
                            'best_addr_sim': round(best_addr_sim, 3),
                            'distance_m': round(best_dist, 1)
                            if best_dist != float('inf')
                            else None,
                        }
                    )

        # 상세 통계 로깅
        logger.info(
            'fuzzy_matching_summary',
            tourist_count=len(tourist),
            primary_count=len(primary),
            matched=matched_count,
            high_confidence=high_confidence,
            medium_confidence=medium_confidence,
            unmatched=len(still_unmatched),
            filtered_by_low_similarity=filtered_by_low_similarity,
            filtered_by_low_score=filtered_by_low_score,
        )

        # 실패 케이스 상세 출력 (TOP 5)
        if sample_debug_logs:
            logger.info(
                'fuzzy_matching_failed_samples',
                samples=sample_debug_logs,
            )

        final = primary + general

        # 🆕 매칭 실패한 TouristInfo도 독립 배지로 유지
        if still_unmatched:
            for t in still_unmatched:
                # TouristInfo 자체를 Primary처럼 취급 (독립 배지)
                if 'tourist_info' not in t.certs:
                    t.certs.append('tourist_info')
                final.append(t)

            logger.info(
                'tourist_info_kept_as_independent',
                count=len(still_unmatched),
            )

        return final

    def _merge_by_general_status(self, records: list[CertRecord]) -> list[CertRecord]:
        """일반음식점 상태 정보 매칭 (이름 + 주소 기반)

        전략:
        1. 지역별 인덱싱 (Spatial Hashing)으로 O(N²) → O(N×K) 최적화
        2. 이름 유사도 80%+ 또는 (이름 60%+ AND 주소 70%+)
        3. 상태 정보 (영업중/폐업/위생등급) 병합

        Args:
            records: 정규화된 레코드 목록

        Returns:
            상태 정보가 병합된 레코드 목록
        """
        certified_sources = {
            'safe_restaurant',
            'low_sodium',
            'model_restaurant',
            'food_safety',
            'tourist_info',
        }

        primary = [r for r in records if r.source in certified_sources]
        general = [r for r in records if r.source == 'general_restaurant']

        if not general:
            logger.info('general_status_matching_skip_no_general')
            return records

        # 🆕 지역별 인덱싱 (O(1) 조회)
        general_index: dict[str, list[CertRecord]] = {}
        for g in general:
            key = f"{g.province or ''}|{g.municipality or ''}|{g.emd or ''}"
            if key not in general_index:
                general_index[key] = []
            general_index[key].append(g)

        logger.info(
            'general_status_matching_start',
            primary_count=len(primary),
            general_count=len(general),
            index_keys=len(general_index),
        )

        matched_count = 0
        active_updated = 0
        hygiene_updated = 0

        for p in primary:
            key = f"{p.province or ''}|{p.municipality or ''}|{p.emd or ''}"
            candidates = general_index.get(key, [])

            best_match = None
            best_score = 0.0
            best_name_sim = 0.0
            best_addr_sim = 0.0

            for g in candidates:
                name_sim = self._name_similarity(p.raw_name, g.raw_name)
                addr_sim = self._address_similarity(p.raw_address, g.raw_address)

                # 매칭 조건: 이름 80%+ 또는 (이름 60%+ AND 주소 70%+)
                if name_sim >= 0.8 or (name_sim >= 0.6 and addr_sim >= 0.7):
                    score = name_sim * 0.7 + addr_sim * 0.3
                    if score > best_score:
                        best_score = score
                        best_match = g
                        best_name_sim = name_sim
                        best_addr_sim = addr_sim

            if best_match:
                matched_count += 1

                # 🆕 상태 정보 병합
                if hasattr(best_match, 'is_active') and not best_match.is_active:
                    if p.is_active:  # 이전에 활성이었다면
                        p.is_active = False
                        p.closed_at = best_match.closed_at
                        active_updated += 1

                if hasattr(best_match, 'hygiene_grade') and best_match.hygiene_grade:
                    if not p.hygiene_grade:  # 이전에 없었으면
                        p.hygiene_grade = best_match.hygiene_grade
                        hygiene_updated += 1

                logger.debug(
                    'general_status_matched',
                    primary=p.raw_name,
                    general=best_match.raw_name,
                    name_sim=round(best_name_sim, 3),
                    addr_sim=round(best_addr_sim, 3),
                    is_active=best_match.is_active,
                    hygiene_grade=best_match.hygiene_grade,
                )

        logger.info(
            'general_status_matching_complete',
            primary_count=len(primary),
            general_count=len(general),
            matched=matched_count,
            active_updated=active_updated,
            hygiene_updated=hygiene_updated,
        )

        return records

    def merge(self, records: list[CertRecord]) -> list[CertRecord]:
        """
        레코드 병합 (dedup_key 기반 + 멀티 키 스코어 매칭)

        Args:
            records: 정규화된 레코드 목록

        Returns:
            병합된 레코드 목록
        """
        logger.info(
            'merge_start',
            count=len(records),
            matching_radius_m=self.matching_radius_m,
        )

        # Phase 1: dedup_key 기반 병합
        merged = self._merge_by_dedup_key(records)

        # Phase 2: 멀티 키 스코어 기반 매칭 (이름 + 주소 + 좌표)
        merged = self._merge_by_fuzzy_matching(merged)

        # 🆕 Phase 3: 일반음식점 상태 정보 병합 (폐업/위생등급)
        merged = self._merge_by_general_status(merged)

        return merged

    def _merge_by_dedup_key(self, records: list[CertRecord]) -> list[CertRecord]:
        """dedup_key 기반 병합"""
        merged: dict[str, CertRecord] = {}

        for record in records:
            if not record.is_valid():
                continue

            if not record.dedup_key:
                record.dedup_key = generate_dedup_key(
                    raw_name=record.raw_name,
                    raw_address=record.raw_address,
                    phone=record.phone,
                    detail_address=record.lot_num or record.road_name,
                )

            if not record.dedup_key:
                continue

            key = record.dedup_key

            if key not in merged:
                if record.cert_type and record.cert_type not in record.certs:
                    record.certs.append(record.cert_type)

                self._preserve_own_coords(record)
                merged[key] = record
            else:
                existing = merged[key]
                self._merge_into(existing, record)

        result = list(merged.values())

        logger.info(
            'merge_by_dedup_key_complete',
            input_count=len(records),
            output_count=len(result),
            merged_count=len(records) - len(result),
        )

        return result

    def _merge_tourist_info(self, primary: CertRecord, tourist: CertRecord) -> None:
        """TouristInfo 정보를 Primary 소스에 병합"""
        if tourist.main_menu and not primary.main_menu:
            primary.main_menu = tourist.main_menu
            primary.main_menu_source = 'tourist_info'

        if tourist.menus:
            primary.menus = sorted(set(primary.menus + tourist.menus))
            primary.menus_source = 'tourist_info'

        if tourist.raw and isinstance(tourist.raw, dict):
            tourist_ext = tourist.raw.get('external_info', {})
            if tourist_ext.get('photos'):
                if primary.raw is None:
                    primary.raw = {}
                if not isinstance(primary.raw, dict):
                    primary.raw = {}
                primary.raw['tourist_photos'] = tourist_ext['photos']

            if tourist_ext.get('business_hours'):
                if primary.raw is None:
                    primary.raw = {}
                if not isinstance(primary.raw, dict):
                    primary.raw = {}
                primary.raw['tourist_business_hours'] = tourist_ext['business_hours']

    def _preserve_own_coords(self, record: CertRecord) -> None:
        """신규 레코드의 자체 좌표를 교차 검증용으로 raw에 보존"""
        if not record.has_coordinate():
            return

        if record.raw is None:
            record.raw = {}

        if not isinstance(record.raw, dict):
            return

        source = record.source

        if source == 'general_restaurant':
            record.raw['general_lat'] = record.lat
            record.raw['general_lng'] = record.lng
            record.raw['general_source'] = 'general_restaurant'

        elif source == 'tourist_info':
            record.raw['tourist_lat'] = record.lat
            record.raw['tourist_lng'] = record.lng
            record.raw['tourist_source'] = 'tourist_info'

        elif source == 'model_restaurant':
            record.raw['model_lat'] = record.lat
            record.raw['model_lng'] = record.lng
            record.raw['model_source'] = 'model_restaurant'

    def _merge_into(self, existing: CertRecord, new: CertRecord) -> None:
        """기존 레코드에 새 레코드 병합"""
        if new.cert_type and new.cert_type not in existing.certs:
            existing.certs.append(new.cert_type)

        if self._is_better_name(new.raw_name, existing.raw_name):
            existing.raw_name = new.raw_name

        if not existing.phone and new.phone:
            existing.phone = new.phone

        if not existing.biz_type and new.biz_type:
            existing.biz_type = new.biz_type

        self._merge_main_menu(existing, new)

        if new.menus:
            existing.menus = sorted(set(existing.menus + new.menus))
            if new.menus_source:
                existing.menus_source = new.menus_source

        if not existing.designated_at and new.designated_at:
            existing.designated_at = new.designated_at

        if not existing.has_coordinate() and new.has_coordinate():
            existing.lat = new.lat
            existing.lng = new.lng

        if not existing.province and new.province:
            existing.province = new.province
        if not existing.municipality and new.municipality:
            existing.municipality = new.municipality
        if not existing.emd and new.emd:
            existing.emd = new.emd

        if not existing.road_name and new.road_name:
            existing.road_name = new.road_name
        if not existing.lot_num and new.lot_num:
            existing.lot_num = new.lot_num

        if new.raw_address and len(new.raw_address) > len(existing.raw_address or ''):
            existing.raw_address = new.raw_address

        self._merge_cross_validation_coords(existing, new)
        self._merge_quality_score(existing, new)
        self._merge_tags(existing, new)
        self._merge_store_name_quality(existing, new)

    def _merge_cross_validation_coords(self, existing: CertRecord, new: CertRecord) -> None:
        """교차 검증용 좌표 보존"""
        if not new.has_coordinate():
            return

        if existing.raw is None:
            existing.raw = {}

        if not isinstance(existing.raw, dict):
            return

        source = new.source

        if source == 'general_restaurant':
            existing.raw['general_lat'] = new.lat
            existing.raw['general_lng'] = new.lng
            existing.raw['general_source'] = 'general_restaurant'

        elif source == 'tourist_info':
            existing.raw['tourist_lat'] = new.lat
            existing.raw['tourist_lng'] = new.lng
            existing.raw['tourist_source'] = 'tourist_info'

        elif source == 'model_restaurant':
            existing.raw['model_lat'] = new.lat
            existing.raw['model_lng'] = new.lng
            existing.raw['model_source'] = 'model_restaurant'

    def _merge_quality_score(self, existing: CertRecord, new: CertRecord) -> None:
        """quality_score 병합 (높은 값 유지)"""
        if new.quality_score is None:
            return

        if existing.quality_score is None:
            existing.quality_score = new.quality_score
            return

        if new.quality_score > existing.quality_score:
            existing.quality_score = new.quality_score

    def _merge_tags(self, existing: CertRecord, new: CertRecord) -> None:
        """7차원 태그 배열 통합 (합집합 + 정렬)"""
        tag_fields = [
            'cuisine_tags',
            'nutrient_tags',
            'cooking_tags',
            'ingredient_tags',
            'regional_tags',
            'allergen_tags',
            'occasion_tags',
            'avoid_tags',
        ]

        for field in tag_fields:
            existing_tags = getattr(existing, field, None) or []
            new_tags = getattr(new, field, None) or []

            if new_tags:
                merged_tags = sorted(set(existing_tags + new_tags))
                setattr(existing, field, merged_tags)

    def _merge_store_name_quality(self, existing: CertRecord, new: CertRecord) -> None:
        """store_name_quality 병합 (우선순위 기반)"""
        if not new.store_name_quality:
            return

        if not existing.store_name_quality:
            existing.store_name_quality = new.store_name_quality
            return

        existing_priority = self.QUALITY_PRIORITY.get(existing.store_name_quality, 0)
        new_priority = self.QUALITY_PRIORITY.get(new.store_name_quality, 0)

        if new_priority > existing_priority:
            existing.store_name_quality = new.store_name_quality

    def _merge_main_menu(self, existing: CertRecord, new: CertRecord) -> None:
        """대표 메뉴 병합 (우선순위 기반)"""
        if not new.main_menu:
            return

        if not existing.main_menu:
            existing.main_menu = new.main_menu
            existing.main_menu_source = new.main_menu_source
            return

        existing_priority = self.MENU_SOURCE_PRIORITY.get(existing.main_menu_source or '', 0)
        new_priority = self.MENU_SOURCE_PRIORITY.get(new.main_menu_source or '', 0)

        if new_priority > existing_priority:
            existing.main_menu = new.main_menu
            existing.main_menu_source = new.main_menu_source
        elif new_priority == existing_priority:
            if len(new.main_menu) > len(existing.main_menu):
                existing.main_menu = new.main_menu
                existing.main_menu_source = new.main_menu_source

    def _is_better_name(self, new_name: str, existing_name: str) -> bool:
        """더 나은 상호명인지 판단"""
        new_valid = self._normalizer.is_valid_name(new_name)
        existing_valid = self._normalizer.is_valid_name(existing_name)

        if new_valid and not existing_valid:
            return True
        if new_valid and existing_valid:
            return len(new_name) > len(existing_name)
        return False


def merge_records(records: list[CertRecord]) -> list[CertRecord]:
    """레코드 목록 병합 (편의 함수)"""
    merger = Merger()
    return merger.merge(records)
