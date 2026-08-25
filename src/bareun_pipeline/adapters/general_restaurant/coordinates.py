# ==============================================================================
# 파일 1: src/bareun_pipeline/adapters/general_restaurant/coordinates.py
# ==============================================================================
"""좌표계 변환 유틸리티 (EPSG → WGS84)"""

from __future__ import annotations

from ...observability.logging import get_logger

logger = get_logger(__name__)

# 한국 공공데이터 자주 사용 좌표계 후보
EPSG_CANDIDATES = [
    ('EPSG:5174', 'Korea 1985 Modified Central Belt'),
    ('EPSG:5179', 'Korea 2000 Unified CS'),
    ('EPSG:5181', 'Korea 2000 Central Belt'),
    ('EPSG:5185', 'Korea 2000 West Belt'),
    ('EPSG:5186', 'Korea 2000 Central Belt 2010'),
    ('EPSG:5187', 'Korea 2000 East Belt'),
    ('EPSG:5188', 'Korea 2000 East Sea Belt'),
    ('EPSG:2097', 'Korean 1985 Central Belt'),
    ('EPSG:2096', 'Korean 1985 East Belt'),
]

KOREA_LAT_MIN, KOREA_LAT_MAX = 33.0, 38.7
KOREA_LNG_MIN, KOREA_LNG_MAX = 124.5, 132.0


def convert_to_wgs84(x: float, y: float) -> tuple[float | None, float | None]:
    """다양한 한국 좌표계를 WGS84(lat, lng)로 변환"""
    if 124.5 <= x <= 132.0 and 33.0 <= y <= 38.6:
        return y, x  # 이미 WGS84

    try:
        from pyproj import Transformer
    except ImportError:
        logger.warning('pyproj_not_installed_coordinate_skipped')
        return None, None

    for epsg, name in EPSG_CANDIDATES:
        try:
            transformer = Transformer.from_crs(epsg, 'EPSG:4326', always_xy=True)
            lng, lat = transformer.transform(x, y)
            if KOREA_LAT_MIN <= lat <= KOREA_LAT_MAX and KOREA_LNG_MIN <= lng <= KOREA_LNG_MAX:
                logger.debug('coordinate_converted', epsg=epsg, x=x, y=y, lat=lat, lng=lng)
                return lat, lng
        except Exception:
            continue

    logger.warning('coordinate_convert_failed', x=x, y=y)
    return None, None
