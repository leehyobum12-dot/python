"""
Bareun Pipeline: Tourist Info Mapper
=====================================
- TourAPI 응답(Dict) → CertRecord 도메인 모델 변환
- 메뉴, 영업시간, 휴무일, 사진 추출 순수 로직
- I/O 없음, 외부 의존성 없음

위치: src/bareun_pipeline/adapters/tourist_info/mapper.py
"""

from __future__ import annotations

import re
from typing import Any

from ...domain.address import parse_addr_tokens
from ...domain.record import CertRecord

FOOD_CONTENT_TYPE_ID = 39


def parse_menus(intro: dict[str, Any]) -> tuple[str | None, list[str]]:
    """메뉴 파싱 (firstmenu, treatmenu)"""
    main_menu: str | None = None
    menus: list[str] = []

    first_menu = str(intro.get('firstmenu', '')).strip()
    if first_menu and 2 <= len(first_menu) <= 50:
        main_menu = first_menu

    treat_menu = str(intro.get('treatmenu', '')).strip()
    if treat_menu:
        parts = re.split(r'[,;/|\n·]', treat_menu)
        for part in parts:
            part = re.sub(r'<[^>]+>', '', part).strip()
            part = re.sub(r'\s*\d+[,.]?\d*원?\s*$', '', part).strip()
            if part and 2 <= len(part) <= 50:
                menus.append(part)

    if not main_menu and menus:
        main_menu = menus[0]
    if main_menu and main_menu in menus:
        menus.remove(main_menu)

    return main_menu, menus[:20]


def parse_business_hours(open_time: str) -> dict[str, Any]:
    """
    영업시간 파싱

    지원 패턴:
    - 단일: "11:00-22:00", "11시~22시", "오전 11:00 - 오후 10:00"
    - 브레이크타임: "11:00-15:00, 17:00-22:00" (2개 시간대)
    """
    if not open_time:
        return {}

    # 🆕 브레이크타임 패턴: 쉼표로 구분된 2개 이상의 시간대
    parts = re.split(r'[,;/]', open_time)
    time_ranges: list[str] = []

    for part in parts:
        part = part.strip()
        # 시간 패턴: 11:00, 11시, 오전 11:00 등
        match = re.search(
            r'(\d{1,2})[:시]\s*(\d{2})?\s*[-~]\s*(\d{1,2})[:시]\s*(\d{2})?',
            part,
        )
        if match:
            start_h, start_m = match.group(1), match.group(2) or '00'
            end_h, end_m = match.group(3), match.group(4) or '00'
            time_ranges.append(f'{start_h.zfill(2)}:{start_m}-{end_h.zfill(2)}:{end_m}')

    if not time_ranges:
        return {}

    # 단일 시간대: daily, 복수: break_start/break_end
    if len(time_ranges) == 1:
        return {'daily': time_ranges[0]}
    elif len(time_ranges) == 2:
        return {
            'open': time_ranges[0].split('-')[0],
            'break_start': time_ranges[0].split('-')[1],
            'break_end': time_ranges[1].split('-')[0],
            'close': time_ranges[1].split('-')[1],
        }
    else:
        return {'daily': time_ranges[0]}


def parse_rest_days(rest_days: str) -> list[str]:
    """휴무일 파싱 (명시적 매칭)"""
    if not rest_days:
        return []

    DAY_MAP: dict[str, str] = {
        '월': 'monday',
        '화': 'tuesday',
        '수': 'wednesday',
        '목': 'thursday',
        '금': 'friday',
        '토': 'saturday',
        '일': 'sunday',
    }

    result: list[str] = []
    # 🆕 정확한 매칭: 단어 경계 확인
    for kor, eng in DAY_MAP.items():
        # "월요일", "월", "매주 월" 등 매칭
        if re.search(rf'{kor}요일|{kor}\s*(요일|$)', rest_days):
            result.append(eng)

    return sorted(set(result))


def extract_photos(item: dict[str, Any], detail: dict[str, Any]) -> list[dict[str, str]]:
    """
    사진 URL 추출 (목록 + 상세)

    🐛 버그 수정: 카테고리 추측 제거
    - 기존: 'exterior'/'interior'로 추측 (부정확)
    - 수정: 'unknown'으로 통일 (Phase 11에서 실제 분류)
    """
    photos: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    def add_photo(url: str, source: str) -> None:
        url = url.strip()
        if url and url not in seen_urls:
            seen_urls.add(url)
            photos.append(
                {
                    'url': url,
                    # 🐛 버그 수정: 추측 제거 ('exterior'/'interior' → 'unknown')
                    'category': 'unknown',
                    'source': source,
                }
            )

    # 목록 응답에서 추출
    add_photo(str(item.get('firstimage', '')), 'list')
    add_photo(str(item.get('firstimage2', '')), 'list')

    # 상세 응답에서 추출 (중복 방지)
    if detail:
        add_photo(str(detail.get('firstimage', '')), 'detail')
        add_photo(str(detail.get('firstimage2', '')), 'detail')

    return photos


def map_to_record(
    item: dict[str, Any],
    detail: dict[str, Any],
    intro: dict[str, Any],
    source: str,
    cert_type: str,
) -> CertRecord | None:
    """
    TourAPI 아이템을 CertRecord로 매핑

    Args:
        item: areaBasedList2 응답의 개별 아이템
        detail: detailCommon2 응답
        intro: detailIntro2 응답
        source: 어댑터 소스명
        cert_type: 인증 타입

    Returns:
        CertRecord 또는 None (유효하지 않은 데이터)
    """
    content_id = int(item.get('contentid', 0))
    raw_name = str(item.get('title', '')).strip()
    addr1 = str(item.get('addr1', '')).strip()
    addr2 = str(item.get('addr2', '')).strip()
    raw_address = f'{addr1} {addr2}'.strip()

    if not raw_name or not raw_address:
        return None

    p, m, e = parse_addr_tokens(raw_address)
    main_menu, menus = parse_menus(intro)
    business_hours = parse_business_hours(str(intro.get('opentimefood', '')))
    rest_days = parse_rest_days(str(intro.get('restdatefood', '')))
    photos = extract_photos(item, detail)

    lat: float | None = None
    lng: float | None = None
    if item.get('mapy') and item.get('mapx'):
        try:
            lat, lng = float(item['mapy']), float(item['mapx'])
        except (ValueError, TypeError):
            pass

    external_info: dict[str, Any] = {
        'content_id': content_id,
        'business_hours': business_hours,
        'rest_days': rest_days,
        'photos': photos,
        'tel': str(detail.get('tel', '')).strip() or str(item.get('tel', '')).strip(),
        'homepage': str(detail.get('homepage', '')).strip(),
        'source': source,
    }

    return CertRecord(
        source=source,
        cert_type=cert_type,
        raw_name=raw_name,
        raw_address=raw_address,
        province=p,
        municipality=m,
        emd=e,
        road_name=addr1 or None,
        lot_num=addr2 or None,
        lat=lat,
        lng=lng,
        phone=external_info['tel'] or None,
        main_menu=main_menu,
        main_menu_source=source if main_menu else None,
        menus=menus,
        menus_source=source if menus else None,
        is_active=True,
        raw={'content_id': content_id, 'external_info': external_info},
    )
