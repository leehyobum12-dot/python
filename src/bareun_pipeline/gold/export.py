"""
Gold Layer: 데이터 내보내기
=============================
- NDJSON (Supabase 적재용)
- CSV (사람/엑셀 검수용)

기존 참고:
- pipeline.py의 CSV 출력 로직
- utf-8-sig 인코딩 (Excel 호환)

보존 사항:
- CSV utf-8-sig 인코딩
- 배열을 파이프 문자열로 변환

개선 사항:
- NDJSON + CSV 이중 출력
- GoldExporter 클래스로 캡슐화
"""

from __future__ import annotations

import csv
import json
import os
from datetime import datetime
from typing import TypedDict

from ..observability.logging import get_logger
from ..settings import get_settings
from .transform import GoldRestaurant

logger = get_logger(__name__)


class ExportResult(TypedDict):
    """내보내기 결과"""

    ndjson_path: str | None
    csv_path: str | None
    count: int


class GoldExporter:
    """
    Gold 데이터 내보내기

    사용법:
        exporter = GoldExporter()
        result = exporter.export_dual(records)

    테스트용:
        exporter = GoldExporter(gold_dir='/tmp/test')
    """

    # CSV 컬럼 정의
    CSV_FIELDNAMES = [
        'dedup_key',
        'data_source',
        'store_name',
        'province',
        'municipality',
        'emd',
        'address_road',
        'address_lotnum',
        'lat',
        'lng',
        'main_menu',
        'main_menu_source',
        'menus',
        'menus_source',
        'phone',
        'biz_type',
        'certs',
        'designated_at',
        'cuisine_tags',
        'nutrient_tags',
        'cooking_tags',
        'ingredient_tags',
        'regional_tags',
        'allergen_tags',
        'occasion_tags',
        'avoid_tags',
        'quality_score',
    ]

    def __init__(self, gold_dir: str | None = None):
        """
        GoldExporter 초기화

        Args:
            gold_dir: Gold 데이터 저장 디렉토리 (None이면 settings.gold_dir 사용)
        """
        if gold_dir is None:
            self.settings = get_settings()
            self._gold_dir = self.settings.gold_dir
        else:
            self.settings = None
            self._gold_dir = gold_dir

        os.makedirs(self._gold_dir, exist_ok=True)

    @property
    def gold_dir(self) -> str:
        """Gold 데이터 저장 디렉토리"""
        return self._gold_dir

    def export_ndjson(
        self,
        records: list[GoldRestaurant],
        filename: str | None = None,
    ) -> str:
        """
        NDJSON 형식으로 내보내기 (Supabase 적재용)

        Args:
            records: 내보낼 레코드 목록
            filename: 파일명 (None이면 자동 생성)

        Returns:
            저장된 파일 경로
        """
        if filename is None:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f'gold_{timestamp}.ndjson'

        filepath = os.path.join(self._gold_dir, filename)

        with open(filepath, 'w', encoding='utf-8') as f:
            for record in records:
                f.write(json.dumps(record.to_payload(), ensure_ascii=False) + '\n')

        logger.info('gold_export_ndjson', path=filepath, count=len(records))

        return filepath

    def export_csv(
        self,
        records: list[GoldRestaurant],
        filename: str | None = None,
        with_bom: bool = True,
    ) -> str:
        """
        CSV 형식으로 내보내기 (사람/엑셀 검수용)

        Args:
            records: 내보낼 레코드 목록
            filename: 파일명 (None이면 자동 생성)
            with_bom: True이면 utf-8-sig (Excel 호환)

        Returns:
            저장된 파일 경로
        """
        if filename is None:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f'gold_{timestamp}.csv'

        filepath = os.path.join(self._gold_dir, filename)

        encoding = 'utf-8-sig' if with_bom else 'utf-8'

        with open(filepath, 'w', encoding=encoding, newline='') as f:
            writer = csv.DictWriter(f, fieldnames=self.CSV_FIELDNAMES)
            writer.writeheader()

            for record in records:
                payload = record.to_payload()

                # 배열을 파이프 문자열로 변환 (CSV 호환성)
                row = {
                    'dedup_key': payload['dedup_key'],
                    'data_source': payload['data_source'],
                    'store_name': payload['store_name'],
                    'province': payload['province'],
                    'municipality': payload['municipality'],
                    'emd': payload['emd'] or '',
                    'address_road': payload['address_road'] or '',
                    'address_lotnum': payload['address_lotnum'] or '',
                    'lat': payload['lat'],
                    'lng': payload['lng'],
                    'main_menu': payload['main_menu'] or '',
                    'main_menu_source': payload['main_menu_source'] or '',
                    'menus': '|'.join(payload['menus']),
                    'menus_source': payload['menus_source'] or '',
                    'phone': payload['phone'] or '',
                    'biz_type': payload['biz_type'] or '',
                    'certs': '|'.join(payload['certs']),
                    'designated_at': payload['designated_at'] or '',
                    'cuisine_tags': '|'.join(payload['cuisine_tags']),
                    'nutrient_tags': '|'.join(payload['nutrient_tags']),
                    'cooking_tags': '|'.join(payload['cooking_tags']),
                    'ingredient_tags': '|'.join(payload['ingredient_tags']),
                    'regional_tags': '|'.join(payload['regional_tags']),
                    'allergen_tags': '|'.join(payload['allergen_tags']),
                    'occasion_tags': '|'.join(payload['occasion_tags']),
                    'avoid_tags': '|'.join(payload['avoid_tags']),
                    'quality_score': payload['quality_score'],
                }
                writer.writerow(row)

        logger.info('gold_export_csv', path=filepath, count=len(records))

        return filepath

    def export_dual(
        self,
        records: list[GoldRestaurant],
        filename_base: str | None = None,
    ) -> ExportResult:
        """
        NDJSON + CSV 이중 출력

        Args:
            records: 내보낼 레코드 목록
            filename_base: 파일명 기반 (None이면 자동 생성)

        Returns:
            내보내기 결과
        """
        if filename_base is None:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename_base = f'gold_{timestamp}'

        ndjson_path = self.export_ndjson(records, f'{filename_base}.ndjson')
        csv_path = self.export_csv(records, f'{filename_base}.csv')

        return {
            'ndjson_path': ndjson_path,
            'csv_path': csv_path,
            'count': len(records),
        }


def export_records(records: list[GoldRestaurant], dual: bool = True) -> ExportResult:
    """레코드 내보내기 (편의 함수)"""
    exporter = GoldExporter()
    if dual:
        return exporter.export_dual(records)
    else:
        return {
            'ndjson_path': exporter.export_ndjson(records),
            'csv_path': None,
            'count': len(records),
        }
