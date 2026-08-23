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
from typing import Optional

from ...domain.address import parse_addr_tokens
from ...domain.record import CertRecord


FOOD_CONTENT_TYPE_ID = 39


def parse_menus(intro: dict) -> tuple[Optional[str], list[str]]:
    """메뉴 파싱 (firstmenu, treatmenu)"""
    main_menu = None
    menus = []
    
    first_menu = intro.get('firstmenu', '').strip()
    if first_menu and 2 <= len(first_menu) <= 50:
        main_menu = first_menu
    
    treat_menu = intro.get('treatmenu', '').strip()
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


def parse_business_hours(open_time: str) -> dict[str, str]:
    """영업시간 파싱"""
    if not open_time:
        return {}
    match = re.search(r'(\d{1,2}:\d{2})\s*[-~]\s*(\d{1,2}:\d{2})', open_time)
    return {'daily': f"{match.group(1)}-{match.group(2)}"} if match else {}


def parse_rest_days(rest_days: str) -> list[str]:
    """휴무일 파싱"""
    if not rest_days:
        return []
    DAY_MAP = {
        '월': 'monday', '화': 'tuesday', '수': 'wednesday',
        '목': 'thursday', '금': 'friday', '토': 'saturday', '일': 'sunday',
    }
    return [eng for kor, eng in DAY_MAP.items() if kor in rest_days]


def extract_photos(item: dict, detail: dict) -> list[dict[str, str]]:
    """사진 URL 추출 (목록 + 상세)"""
    photos = []
    
    first_image = item.get('firstimage', '').strip()
    if first_image:
        photos.append({'url': first_image, 'category': 'exterior'})
    
    second_image = item.get('firstimage2', '').strip()
    if second_image:
        photos.append({'url': second_image, 'category': 'interior'})
        
    if detail:
        detail_first = detail.get('firstimage', '').strip()
        if detail_first and detail_first not in [p['url'] for p in photos]:
            photos.append({'url': detail_first, 'category': 'exterior'})
            
        detail_second = detail.get('firstimage2', '').strip()
        if detail_second and detail_second not in [p['url'] for p in photos]:
            photos.append({'url': detail_second, 'category': 'interior'})
            
    return photos


def map_to_record(
    item: dict, 
    detail: dict, 
    intro: dict,
    source: str,
    cert_type: str,
) -> Optional[CertRecord]:
    """TourAPI 아이템을 CertRecord로 매핑"""
    content_id = int(item.get('contentid', 0))
    raw_name = item.get('title', '').strip()
    addr1 = item.get('addr1', '').strip()
    addr2 = item.get('addr2', '').strip()
    raw_address = f"{addr1} {addr2}".strip()

    if not raw_name or not raw_address:
        return None

    p, m, e = parse_addr_tokens(raw_address)
    main_menu, menus = parse_menus(intro)
    business_hours = parse_business_hours(intro.get('opentimefood', ''))
    rest_days = parse_rest_days(intro.get('restdatefood', ''))
    photos = extract_photos(item, detail)

    lat = lng = None
    if item.get('mapy') and item.get('mapx'):
        try:
            lat, lng = float(item['mapy']), float(item['mapx'])
        except (ValueError, TypeError):
            pass

    external_info = {
        'content_id': content_id,
        'business_hours': business_hours,
        'rest_days': rest_days,
        'photos': photos,
        'tel': detail.get('tel', '').strip() or item.get('tel', '').strip(),
        'homepage': detail.get('homepage', '').strip(),
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