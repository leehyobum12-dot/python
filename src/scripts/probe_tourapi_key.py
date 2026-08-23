#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from urllib.parse import unquote

import requests
from dotenv import load_dotenv

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
SRC_DIR = os.path.join(ROOT_DIR, 'src')

if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

load_dotenv()

from bareun_pipeline.settings import get_settings


def mask_key(key: str) -> str:
    if not key:
        return '(empty)'
    if len(key) <= 12:
        return '*' * len(key)
    return f'{key[:6]}...{key[-6:]} len={len(key)}'


def main():
    settings = get_settings()
    raw_key = (settings.data_go_kr_key or '').strip()
    decoded_key = unquote(raw_key) if '%' in raw_key else raw_key

    print('=' * 70)
    print('TourAPI serviceKey probe')
    print('=' * 70)
    print(f'raw_key:     {mask_key(raw_key)}')
    print(f'decoded_key: {mask_key(decoded_key)}')
    print()

    tests = [
        {
            'name': 'KorService2 / areaBasedList2 / decoded key in params',
            'url': 'https://apis.data.go.kr/B551011/KorService2/areaBasedList2',
            'key': decoded_key,
        },
        {
            'name': 'KorService2 / areaBasedList2 / raw key in params',
            'url': 'https://apis.data.go.kr/B551011/KorService2/areaBasedList2',
            'key': raw_key,
        },
        {
            'name': 'KorService1 / areaBasedList1 / decoded key in params',
            'url': 'https://apis.data.go.kr/B551011/KorService1/areaBasedList1',
            'key': decoded_key,
        },
        {
            'name': 'KorService1 / areaBasedList1 / raw key in params',
            'url': 'https://apis.data.go.kr/B551011/KorService1/areaBasedList1',
            'key': raw_key,
        },
    ]

    for t in tests:
        print('-' * 70)
        print(t['name'])

        params = {
            'serviceKey': t['key'],
            'MobileOS': 'ETC',
            'MobileApp': 'bareun',
            '_type': 'json',
            'areaCode': 39,
            'contentTypeId': 39,
            'pageNo': 1,
            'numOfRows': 5,
            'arrange': 'A',
            'listYN': 'Y',
        }

        try:
            r = requests.get(t['url'], params=params, timeout=10)
            print('status:', r.status_code)
            print('text:', r.text[:500])
        except Exception as e:
            print('error:', type(e).__name__, e)


if __name__ == '__main__':
    main()