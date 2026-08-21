"""
모범음식점 CSV 어댑터
=======================
- 로컬 CSV 파일 파싱
- 인코딩 자동 감지 (cp949, euc-kr, utf-8)
- 컬럼 인덱스 기반 파싱

기존 참고:
- adapters/model_restaurant.py 전체 구조
- 인코딩 자동 감지 로직
- 컬럼 인덱스 상수

보존 사항:
- 인코딩 자동 감지 (cp949, euc-kr, utf-8, utf-8-sig)
- 컬럼 인덱스 기반 파싱
- 영업 상태 필터링

개선 사항:
- BaseAdapter 상속
- 구조화 로깅
- PII 필드 명시적 정의
- 파일 경로 탐색 로직 강화
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import pandas as pd

from .base import BaseAdapter, parse_addr_tokens
from ..domain.record import CertRecord
from ..observability.logging import get_logger


logger = get_logger(__name__)


class ModelRestaurantAdapter(BaseAdapter):
    """
    모범음식점 CSV 파일 어댑터

    데이터 소스:
    - 식약처 전국모범음식점표준데이터 (CSV 파일)
    - 로컬 파일에서 로드 (API 아님)

    PII 필드:
    - 대표자명
    - 영업자명
    """

    # 어댑터 식별자
    source = 'model_restaurant'
    cert_type = 'model_restaurant'
    quota_provider = None  # 로컬 파일이므로 Quota 미적용

    # PII 필드 정의 (raw_archiver에서 마스킹용)
    pii_fields = {
        '대표자명',
        '영업자명',
    }

    # 컬럼 인덱스 (기존 adapters/model_restaurant.py에서 이관)
    COL_STATUS, COL_CLOSED = 3, 4
    COL_NAME, COL_ROAD, COL_LOT = 6, 7, 8
    COL_PHONE, COL_DESIGNATED, COL_CANCEL = 10, 11, 13
    COL_FOOD_TYPE, COL_MENU = 17, 18
    MIN_COLS = 19

    # 인코딩 후보
    ENCODINGS = ('cp949', 'euc-kr', 'utf-8', 'utf-8-sig')

    def __init__(self, csv_path: Optional[str] = None):
        super().__init__()
        self.csv_path = Path(csv_path) if csv_path else self._find_csv()

    def _find_csv(self) -> Optional[Path]:
        """
        CSV 파일 자동 탐색

        기존 adapters/model_restaurant.py의 find_csv() 함수 보존

        Returns:
            CSV 파일 경로 (없으면 None)
        """
        pipeline_root = Path(self.settings.data_dir).parent

        candidates = [
            pipeline_root / 'data' / '모범음식점정보_2026.csv',
            pipeline_root / 'data' / '모범음식점정보.csv',
            pipeline_root / '모범음식점정보_2026.csv',
        ]

        for p in candidates:
            if p.exists():
                logger.info('model_restaurant_csv_found', path=str(p))
                return p

        # glob 패턴 검색
        for d in (pipeline_root / 'data', pipeline_root):
            if d.exists():
                matches = sorted(d.glob('*모범음식점*.csv'))
                if matches:
                    logger.info(
                        'model_restaurant_csv_found_glob',
                        path=str(matches[0]),
                    )
                    return matches[0]

        logger.warning('model_restaurant_csv_not_found')
        return None

    def _read(self) -> Optional[pd.DataFrame]:
        """
        CSV 파일 로드 (인코딩 자동 감지)

        기존 adapters/model_restaurant.py의 _read() 함수 보존

        Returns:
            DataFrame (실패 시 None)
        """
        if self.csv_path is None or not self.csv_path.exists():
            logger.warning(
                'model_restaurant_csv_not_found',
                path=str(self.csv_path),
            )
            return None

        for enc in self.ENCODINGS:
            try:
                df = pd.read_csv(
                    self.csv_path,
                    encoding=enc,
                    header=None,
                    dtype=str,
                    on_bad_lines='skip',
                )
                if df.shape[1] >= self.MIN_COLS:
                    logger.info(
                        'model_restaurant_csv_loaded',
                        encoding=enc,
                        rows=len(df),
                        cols=df.shape[1],
                    )
                    return df
            except (UnicodeDecodeError, pd.errors.ParserError) as e:
                logger.debug(
                    'model_restaurant_encoding_failed',
                    encoding=enc,
                    error=str(e),
                )
                continue

        logger.error(
            'model_restaurant_csv_parse_failed',
            path=str(self.csv_path),
        )
        return None

    def collect(self, province: Optional[str] = None) -> List[CertRecord]:
        """
        데이터 수집

        Args:
            province: 시도명 (None이면 전국)

        Returns:
            수집된 CertRecord 목록 (활성 상태만)
        """
        df = self._read()
        if df is None:
            return []

        logger.info(
            'model_restaurant_collect_start',
            province=province,
            rows=len(df),
        )

        def clean(v):
            s = str(v).strip() if pd.notna(v) else ''
            return s if s not in ('', 'nan', 'NaN', 'None', 'NaT') else None

        records: List[CertRecord] = []

        for _, row in df.iterrows():
            status = clean(row.iloc[self.COL_STATUS]) or ''
            cancel_dt = clean(row.iloc[self.COL_CANCEL])
            closed_dt = clean(row.iloc[self.COL_CLOSED])

            if status != '영업' or cancel_dt or closed_dt:
                continue

            road = clean(row.iloc[self.COL_ROAD])
            lot = clean(row.iloc[self.COL_LOT])
            address = road or lot

            if not address:
                continue

            if province and not address.startswith(province):
                continue

            raw = {str(k): (None if pd.isna(v) else str(v)) for k, v in row.items()}
            self.raw_rows.append(raw)

            p, m, e = parse_addr_tokens(address)

            record = CertRecord(
                source=self.source,
                cert_type=self.cert_type,
                raw_name=clean(row.iloc[self.COL_NAME]) or '',
                raw_address=address,
                province=p,
                municipality=m,
                emd=e,
                road_name=road,
                lot_num=lot,
                phone=clean(row.iloc[self.COL_PHONE]),
                biz_type=clean(row.iloc[self.COL_FOOD_TYPE]),
                main_menu=clean(row.iloc[self.COL_MENU]),
                designated_at=clean(row.iloc[self.COL_DESIGNATED]),
                cancelled_at=cancel_dt,
                closed_at=closed_dt,
                is_active=True,
                raw=raw,
            )
            records.append(record)

        valid_records = [r for r in records if r.is_valid()]
        logger.info(
            'model_restaurant_collect_complete',
            count=len(valid_records),
            total=len(records),
        )

        return valid_records