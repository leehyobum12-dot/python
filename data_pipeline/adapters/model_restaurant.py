# data_pipeline/adapters/model_restaurant.py
import pandas as pd
from pathlib import Path
from typing import List, Optional
from .base import BaseAdapter, CertRecord, parse_addr_tokens

ADAPTER_DIR = Path(__file__).resolve().parent
PIPE_DIR = ADAPTER_DIR.parent
ROOT_DIR = PIPE_DIR.parent

COL_STATUS, COL_CLOSED = 3, 4
COL_NAME, COL_ROAD, COL_LOT = 6, 7, 8
COL_PHONE, COL_DESIGNATED, COL_CANCEL = 10, 11, 13
COL_FOOD_TYPE, COL_MENU = 17, 18
MIN_COLS = 19


def find_csv() -> Optional[Path]:
    candidates = [
        PIPE_DIR / "data" / "모범음식점정보_2026.csv",
        PIPE_DIR / "data" / "모범음식점정보.csv",
        ROOT_DIR / "모범음식점정보_2026.csv",
        ROOT_DIR / "data" / "모범음식점정보_2026.csv",
    ]
    for p in candidates:
        if p.exists():
            return p
    for d in (PIPE_DIR / "data", ROOT_DIR):
        if d.exists():
            for f in sorted(d.glob("*모범음식점*.csv")):
                return f
    return None


class ModelRestaurantAdapter(BaseAdapter):
    """식약처 전국모범음식점표준데이터 (CSV 파일)"""

    def __init__(self, csv_path: Optional[str] = None):
        self.csv_path = Path(csv_path) if csv_path else find_csv()
        self.raw_rows = []

    def _read(self) -> Optional[pd.DataFrame]:
        if self.csv_path is None or not self.csv_path.exists():
            print(f"⚠️ [model_restaurant] CSV 없음: {self.csv_path} — 스킵")
            return None
        for enc in ('cp949', 'euc-kr', 'utf-8'):
            try:
                df = pd.read_csv(self.csv_path, encoding=enc, header=None,
                                 dtype=str, on_bad_lines='skip')
                if df.shape[1] >= MIN_COLS:
                    print(f"✅ [model_restaurant] 로드 성공 ({enc}, {len(df)}행)")
                    return df
            except (UnicodeDecodeError, pd.errors.ParserError):
                continue
        # ✅ [수정] 잘린 print문 복원
        print("⚠️ [model_restaurant] 인코딩/파싱 실패 — 스킵")
        return None

    def collect(self, province: Optional[str] = None) -> List[CertRecord]:
        df = self._read()
        if df is None:
            return []

        def clean(v):
            s = str(v).strip() if pd.notna(v) else ''
            return s if s not in ('', 'nan', 'NaN', 'None', 'NaT') else None

        records = []
        for _, row in df.iterrows():
            status = clean(row.iloc[COL_STATUS]) or ''
            cancel_dt = clean(row.iloc[COL_CANCEL])
            closed_dt = clean(row.iloc[COL_CLOSED])
            if status != '영업' or cancel_dt or closed_dt:
                continue

            road = clean(row.iloc[COL_ROAD])
            lot = clean(row.iloc[COL_LOT])
            address = road or lot
            if not address:
                continue
            if province and not address.startswith(province):
                continue

            raw = {k: (None if pd.isna(v) else str(v)) for k, v in row.items()}
            self.raw_rows.append(raw)
            p, m, e = parse_addr_tokens(address)

            records.append(CertRecord(
                source='mfds-model', cert_type='model_restaurant',
                raw_name=clean(row.iloc[COL_NAME]) or '',
                raw_address=address, province=p, municipality=m, emd=e,
                road_name=road, lot_num=lot,
                phone=clean(row.iloc[COL_PHONE]),
                biz_type=clean(row.iloc[COL_FOOD_TYPE]),
                main_menu=clean(row.iloc[COL_MENU]),
                designated_at=clean(row.iloc[COL_DESIGNATED]),
                cancelled_at=cancel_dt, closed_at=closed_dt,
                is_active=True, raw=raw,
            ))

        print(f"✅ [model_restaurant] {len(records)}건 수집 (활성 필터 통과)")
        return records