#!/usr/bin/env python3
"""
CSV 품질 검수 스크립트
========================
- 쓰레기 의심 메뉴 검출
- 길이 초과 메뉴 검출
- 가게명 포함 메뉴 검출
- 출처별 샘플 출력
- 품질 점수 분포 출력

기존 참고:
- verify_jeju.py 전체 구조

사용법:
    python scripts/verify_output.py data/gold/gold_20250101_120000.csv
"""
from __future__ import annotations

import sys
import os

# 경로 설정
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
SRC_DIR = os.path.join(ROOT_DIR, 'src')

if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

import pandas as pd
import re


def verify_csv(csv_path: str) -> None:
    """CSV 품질 검수"""
    if not os.path.exists(csv_path):
        print(f'❌ 파일 없음: {csv_path}')
        return

    df = pd.read_csv(csv_path, encoding='utf-8-sig')
    print(f'총 레코드: {len(df)}건')
    print(f'메뉴 발견: {df["main_menu"].notna().sum()}건')
    print(f'메뉴 NULL: {df["main_menu"].isna().sum() + (df["main_menu"] == "").sum()}건')

    # ─── 1. 쓰레기 의심 메뉴 (문장, 조사, 가게명 포함) ───
    print('\n' + '=' * 70)
    print('🚨 쓰레기 의심 메뉴 (문장/조사/가게명 포함)')
    print('=' * 70)
    trash_pattern = r'입니다|했어요|했습니다|가게|식당|메뉴판|내돈내산|추천|에서|그리고|하지만'
    trash = df[df['main_menu'].fillna('').str.contains(trash_pattern, regex=True)]
    print(f'총 {len(trash)}건\n')
    for _, r in trash.head(20).iterrows():
        print(f'  [{r.get("main_menu_source", "")}] {r.get("store_name", "")} → "{r.get("main_menu", "")}"')

    # ─── 2. 길이 초과 메뉴 (15자 이상) ───
    print('\n' + '=' * 70)
    print('🚨 15자 초과 메뉴 (비정상)')
    print('=' * 70)
    too_long = df[df['main_menu'].fillna('').str.len() > 15]
    print(f'총 {len(too_long)}건\n')
    for _, r in too_long.head(20).iterrows():
        menu = r.get('main_menu', '')
        print(f'  [{r.get("main_menu_source", "")}] {r.get("store_name", "")} → "{menu}" ({len(menu)}자)')

    # ─── 3. 가게명 포함 메뉴 (상호명 오인) ───
    print('\n' + '=' * 70)
    print('🚨 가게명 포함 메뉴 (상호명 오인 의심)')
    print('=' * 70)
    store_contaminated = df[
        df.apply(
            lambda r: bool(r.get('main_menu')) and bool(r.get('store_name')) and
            (str(r.get('store_name', ''))[:3] in str(r.get('main_menu', ''))),
            axis=1
        )
    ]
    print(f'총 {len(store_contaminated)}건\n')
    for _, r in store_contaminated.head(20).iterrows():
        print(f'  [{r.get("main_menu_source", "")}] "{r.get("store_name", "")}" → "{r.get("main_menu", "")}"')

    # ─── 4. 출처별 샘플 (품질 눈검증) ───
    print('\n' + '=' * 70)
    print('📋 출처별 샘플 (각 10건)')
    print('=' * 70)
    for source in ['original', 'cross_source', 'naver_blog_ner', 'naver_blog_llm_verified', 'naver_blog_llm_filled']:
        subset = df[df.get('main_menu_source', '') == source].head(10)
        if len(subset) > 0:
            print(f'\n[{source}] ({len(df[df.get("main_menu_source", "") == source])}건 중 10건)')
            for _, r in subset.iterrows():
                menus = r.get('menus', '') if pd.notna(r.get('menus')) else ''
                print(f'  {r.get("store_name", "")} | main="{r.get("main_menu", "")}" | sub="{menus}"')

    # ─── 5. 품질 점수 분포 ───
    print('\n' + '=' * 70)
    print('📊 품질 점수 분포')
    print('=' * 70)
    if 'quality_score' in df.columns:
        print(df['quality_score'].describe())
        print(f'\n90점 이상: {(df["quality_score"] >= 90).sum()}건')
        print(f'70~89점: {((df["quality_score"] >= 70) & (df["quality_score"] < 90)).sum()}건')
        print(f'70점 미만: {(df["quality_score"] < 70).sum()}건')
    else:
        print('quality_score 컬럼 없음')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('사용법: python scripts/verify_output.py <csv_path>')
        sys.exit(1)

    verify_csv(sys.argv[1])