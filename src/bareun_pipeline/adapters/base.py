"""
어댑터 베이스 클래스 + 주소 처리 유틸리티
============================================
- HTTP 세션 관리 (재시도, 타임아웃)
- Circuit Breaker 패턴 (연속 실패 시 차단)
- 주소 판별/파싱 유틸리티
- 행정구역 표준화 (통계청 기준)
- QuotaManager 연동 인터페이스

기존 참고:
- adapters/base.py: 전체 구조
- 기존 get_session(), 주소 유틸, PROVINCE_CANON 등

보안/안정성 개선:
- 전남광주통합특별시 제거 → 표준 행정구역
- Circuit Breaker 추가
- connect/read 타임아웃 분리
- 구조화 로깅 연동
"""
from __future__ import annotations

import re
import time
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..domain.record import CertRecord
from ..observability.logging import get_logger
from ..settings import get_settings


logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════
# 주소 판별 유틸리티 (기존 adapters/base.py에서 이관)
# ═══════════════════════════════════════════════════════════

EMD_SUFFIXES: Tuple[str, ...] = ('동', '읍', '면', '리', '가')
ROAD_SUFFIXES: Tuple[str, ...] = ('로', '길', '대로')
SIGUNGU_SUFFIX: Tuple[str, ...] = ('시', '군', '구')


def is_road_name(token: Optional[str]) -> bool:
    """
    도로명 접미사 여부 판별

    Args:
        token: 판별할 토큰

    Returns:
        도로명 접미사(로/길/대로)로 끝나면 True
    """
    if not token:
        return False
    return any(token.endswith(sf) for sf in ROAD_SUFFIXES)


def is_emd_token(token: Optional[str]) -> bool:
    """
    읍면동 접미사 여부 판별

    Args:
        token: 판별할 토큰

    Returns:
        읍면동 접미사(동/읍/면/리/가)로 끝나면 True (도로명 제외)
    """
    if not token:
        return False
    if is_road_name(token):
        return False
    return any(token.endswith(sf) for sf in EMD_SUFFIXES)


def extract_emd_from_addr(addr: str) -> str:
    """
    주소에서 읍면동 추출

    기존 adapters/base.py의 extract_emd_from_addr() 함수 보존

    Args:
        addr: 주소 문자열

    Returns:
        읍면동 토큰 (없으면 빈 문자열)
    """
    if not addr:
        return ''
    s = re.sub(r'\([^)]*\)', '', addr)
    toks = s.split()
    if len(toks) < 3:
        return ''
    for tok in toks[2:]:
        if is_emd_token(tok) and not any(tok.endswith(sf) for sf in SIGUNGU_SUFFIX):
            return tok
    return ''


# ═══════════════════════════════════════════════════════════
# 행정구역 표준화 (통계청 기준)
# ═══════════════════════════════════════════════════════════
# 기존 adapters/base.py의 PROVINCE_CANON에서 이관
# 개선: 전남광주통합특별시 제거 → 표준 행정구역

PROVINCE_CANON: dict[str, str] = {
    '서울': '서울특별시',
    '서울시': '서울특별시',
    '부산': '부산광역시',
    '부산시': '부산광역시',
    '대구': '대구광역시',
    '대구시': '대구광역시',
    '인천': '인천광역시',
    '인천시': '인천광역시',
    '광주': '광주광역시',
    '광주시': '광주광역시',
    '대전': '대전광역시',
    '대전시': '대전광역시',
    '울산': '울산광역시',
    '울산시': '울산광역시',
    '세종': '세종특별자치시',
    '세종시': '세종특별자치시',
    '경기': '경기도',
    '강원': '강원특별자치도',
    '충북': '충청북도',
    '충남': '충청남도',
    '전북': '전북특별자치도',
    '전남': '전라남도',
    '경북': '경상북도',
    '경남': '경상남도',
    '제주': '제주특별자치도',
    '제주도': '제주특별자치도',
}

# 기존 SIDO_ALIAS와 동일 (호환성 유지)
SIDO_ALIAS = PROVINCE_CANON


def canon_province(p: Optional[str]) -> Optional[str]:
    """
    시도명 표준화

    기존 adapters/base.py의 canon_province() 함수 보존

    Args:
        p: 시도명 (원본)

    Returns:
        표준화된 시도명
    """
    if not p:
        return p
    return PROVINCE_CANON.get(p.strip(), p.strip())


def match_prefixes(province: str) -> Tuple[str, ...]:
    """
    시도 접두사 매칭

    기존 adapters/base.py의 match_prefixes() 함수 보존
    개선: 전남광주통합특별시 제거 → 전라남도/광주광역시 분리

    Args:
        province: 시도명

    Returns:
        매칭할 접두사 튜플
    """
    canonical = canon_province(province)
    if not canonical:
        return ()

    # 전라남도와 광주광역시는 별도 처리 (기존 통합 로직 제거)
    if canonical == '전라남도':
        return ('전라남도',)
    if canonical == '광주광역시':
        return ('광주광역시',)

    return (canonical,)


def parse_addr_tokens(addr: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    주소를 파싱하여 (시도, 시군구, 읍면동) 튜플 반환
    """
    if not addr:
        return None, None, None

    addr = re.sub(r'\s+', ' ', addr.strip())

    # 1차 시도: 정규 표현식으로 시도/시군구/읍면동 추출
    match = ADDR_PATTERN.match(addr)
    if match:
        province = match.group('province') or match.group('special') or match.group('sejong')
        
        # 시군구 추출 (안전하게 그룹 접근)
        municipality = None
        for group_name in ('sigungu', 'si2', 'gun_gu', 'gu'):
            try:
                val = match.group(group_name)
                if val:
                    municipality = val
                    break
            except (IndexError, AttributeError):
                continue
        
        # 읍면동 추출 (안전하게 그룹 접근)
        emd = None
        for group_name in ('eup', 'myeon', 'dong'):
            try:
                val = match.group(group_name)
                if val:
                    emd = val
                    break
            except (IndexError, AttributeError):
                continue

        if province:
            province = canon_province(province)

        return province, municipality, emd

    # 2차 시도: 공백으로 분리하여 추출
    tokens = addr.split()

    province = None
    municipality = None
    emd = None

    # 첫 번째 토큰은 시도
    if tokens:
        first = tokens[0]
        if any(first.endswith(suffix) for suffix in PROVINCE_SUFFIXES):
            province = canon_province(first)
            tokens = tokens[1:]
        elif any(first.endswith(suffix) for suffix in ('시', '도')):
            province = canon_province(first)
            tokens = tokens[1:]

    # 두 번째 토큰은 시군구
    if tokens and province:
        second = tokens[0]
        if any(second.endswith(suffix) for suffix in ('시', '군', '구')):
            province_base = province.replace('특별시', '').replace('광역시', '').replace('특별자치도', '').replace('특별자치시', '')
            if not second.startswith(province_base):
                municipality = second
                tokens = tokens[1:]

    # 세 번째 토큰은 읍면동
    if tokens and municipality:
        third = tokens[0]
        if any(third.endswith(suffix) for suffix in ('읍', '면', '동')):
            emd = third
            tokens = tokens[1:]

    # 3차 시도: 주소 전체에서 읍면동 패턴 검색
    if not emd:
        emd_match = re.search(r'([가-힣]+(?:읍|면|동))', addr)
        if emd_match:
            emd = emd_match.group(1)

    return province, municipality, emd

# 정규 표현식 패턴 추가
ADDR_PATTERN = re.compile(
    r'^(?:(?P<special>서울|부산|대구|인천|광주|대전|울산|세종)특별시|'
    r'(?P<sejong>세종특별자치시)|'
    r'(?P<province>경기도|강원도|충청북도|충청남도|전라북도|전라남도|경상북도|경상남도|제주특별자치도)'
    r')\s*'
    r'(?:(?P<si2>[가-힣]+시)|(?P<sigungu>[가-힣]+시|군|구))?\s*'
    r'(?:(?P<eup>[가-힣]+읍)|(?P<myeon>[가-힣]+면)|(?P<dong>[가-힣]+동))?'
)

PROVINCE_SUFFIXES = ('특별시', '광역시', '특별자치도', '특별자치시', '도')


def norm_name(name: str) -> str:
    """
    상호명 정규화

    기존 adapters/base.py의 norm_name() 함수 보존
    """
    return re.sub(r'[^가-힣a-zA-Z0-9]', '', str(name or '')).lower()


def norm_addr(addr: str) -> str:
    """
    주소 정규화

    기존 adapters/base.py의 norm_addr() 함수 보존
    """
    s = re.sub(r'\([^)]*\)', '', addr or '')
    toks = s.split()
    if toks and toks[0] in SIDO_ALIAS:
        toks[0] = SIDO_ALIAS[toks[0]]
    return ''.join(toks).lower()


# ═══════════════════════════════════════════════════════════
# HTTP 세션 팩토리
# ═══════════════════════════════════════════════════════════

def create_session(
    pool_connections: int = 20,
    pool_maxsize: int = 50,
    max_retries: int = 3,
    connect_timeout: int = 5,
    read_timeout: int = 15,
) -> requests.Session:
    """
    HTTP 세션 생성 (재시도 정책 포함)

    기존 adapters/base.py의 get_session() 개선:
    - 재시도 정책을 urllib3.Retry로 명시적 설정
    - 타임아웃을 connect/read로 분리

    Args:
        pool_connections: 연결 풀 크기
        pool_maxsize: 최대 풀 크기
        max_retries: 최대 재시도 횟수
        connect_timeout: 연결 타임아웃 (초)
        read_timeout: 읽기 타임아웃 (초)

    Returns:
        설정된 requests.Session 객체
    """
    session = requests.Session()

    retry_strategy = Retry(
        total=max_retries,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=['GET'],
    )

    adapter = HTTPAdapter(
        pool_connections=pool_connections,
        pool_maxsize=pool_maxsize,
        max_retries=retry_strategy,
        pool_block=False,
    )

    session.mount('http://', adapter)
    session.mount('https://', adapter)

    return session


# ═══════════════════════════════════════════════════════════
# Circuit Breaker
# ═══════════════════════════════════════════════════════════

class CircuitOpenError(Exception):
    """Circuit이 열려 있어 호출 불가"""
    pass


class CircuitBreaker:
    """
    Circuit Breaker 패턴

    연속 실패 시 API 호출을 일시 중단하여 리소스 낭비 방지.
    Netflix Hystrix에서 영감을 받은 구현입니다.

    상태:
    - CLOSED: 정상 작동 (호출 허용)
    - OPEN: 실패 임계값 초과 (호출 차단)
    - HALF-OPEN: 복구 시도 (제한적 호출 허용)

    Attributes:
        _failure_count: 연속 실패 횟수
        _failure_threshold: 실패 임계값
        _recovery_timeout: 복구 대기 시간 (초)
        _last_failure_time: 마지막 실패 시각
        _is_open: Circuit 열림 여부
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: int = 300,
    ):
        self._failure_count = 0
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._last_failure_time: Optional[datetime] = None
        self._is_open = False

    @property
    def is_open(self) -> bool:
        """Circuit 열림 여부"""
        return self._is_open

    @property
    def failure_count(self) -> int:
        """현재 연속 실패 횟수"""
        return self._failure_count

    def call(self, func, *args, **kwargs):
        """
        함수 호출 (Circuit 상태 확인)

        Args:
            func: 호출할 함수
            *args, **kwargs: 함수 인자

        Returns:
            함수 실행 결과

        Raises:
            CircuitOpenError: Circuit이 열려 있을 때
        """
        if self._is_open:
            if self._last_failure_time:
                elapsed = (datetime.now() - self._last_failure_time).total_seconds()
                if elapsed < self._recovery_timeout:
                    raise CircuitOpenError(
                        f'Circuit is open. Wait {self._recovery_timeout - elapsed:.0f}s'
                    )
                else:
                    # Half-open: 시도 허용
                    self._is_open = False
                    self._failure_count = 0
                    logger.info(
                        'circuit_breaker_half_open',
                        elapsed=elapsed,
                    )

        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure()
            raise

    def _on_success(self) -> None:
        """성공 시 상태 초기화"""
        self._failure_count = 0
        self._is_open = False

    def _on_failure(self) -> None:
        """실패 시 카운터 증가"""
        self._failure_count += 1
        self._last_failure_time = datetime.now()

        if self._failure_count >= self._failure_threshold:
            self._is_open = True
            logger.warning(
                'circuit_breaker_open',
                failure_count=self._failure_count,
                recovery_timeout=self._recovery_timeout,
            )

    def reset(self) -> None:
        """수동 리셋"""
        self._failure_count = 0
        self._is_open = False
        self._last_failure_time = None


# ═══════════════════════════════════════════════════════════
# Base Adapter
# ═══════════════════════════════════════════════════════════

class BaseAdapter(ABC):
    """
    어댑터 추상 베이스 클래스

    모든 공공데이터 수집 어댑터는 이 클래스를 상속받아야 합니다.

    서브클래스에서 정의해야 할 속성:
    - source: 어댑터 식별자 (e.g., 'safe_restaurant')
    - cert_type: 인증 타입 (e.g., 'safe_restaurant')
    - quota_provider: QuotaManager provider 이름 (None이면 Quota 미적용)
    - pii_fields: PII 필드 집합 (마스킹용)

    사용법:
        class MyAdapter(BaseAdapter):
            source = 'my_source'
            cert_type = 'my_cert'
            quota_provider = None
            pii_fields = {'OWNER_NAME'}

            def collect(self, province=None, **kwargs):
                ...
    """

    # 서브클래스에서 정의해야 할 속성
    source: str = ''
    cert_type: str = ''
    quota_provider: Optional[str] = None
    pii_fields: set = set()

    def __init__(self):
        self.settings = get_settings()
        self.circuit_breaker = CircuitBreaker(
            failure_threshold=self.settings.http_max_retries + 2,
            recovery_timeout=300,
        )
        self.session = create_session(
            connect_timeout=self.settings.http_timeout_connect,
            read_timeout=self.settings.http_timeout_read,
            max_retries=self.settings.http_max_retries,
        )
        self.raw_rows: list[dict] = []

    @abstractmethod
    def collect(self, province: Optional[str] = None) -> list[CertRecord]:
        """
        데이터 수집 (서브클래스에서 구현)

        Args:
            province: 시도명 (None이면 전국)

        Returns:
            수집된 CertRecord 목록
            
        Note:
            어댑터별 추가 파라미터는 __init__에서 받아야 합니다.
            (예: TouristInfoAdapter(fetch_details=True, max_detail_count=30))
        """
        pass

    def check_quota(self) -> None:
        """
        쿼터 확인 (quota_provider가 설정된 경우)

        QuotaManager는 observability/quota.py에서 정의됩니다.
        quota_provider가 None이면 이 메서드는 아무 동작도 하지 않습니다.
        """
        if self.quota_provider:
            from ..observability.quota import QuotaManager
            quota = QuotaManager.get_instance()
            quota.check_and_consume(self.quota_provider)

    def _safe_request(
        self,
        method: str,
        url: str,
        **kwargs,
    ) -> Optional[requests.Response]:
        """
        안전한 HTTP 요청 (Circuit Breaker + 타임아웃)

        Args:
            method: HTTP 메서드 ('GET', 'POST')
            url: 요청 URL
            **kwargs: requests.request 인자

        Returns:
            Response 객체 (실패 시 None)
        """
        try:
            def do_request():
                return self.session.request(
                    method,
                    url,
                    timeout=(
                        self.settings.http_timeout_connect,
                        self.settings.http_timeout_read,
                    ),
                    **kwargs,
                )

            response = self.circuit_breaker.call(do_request)
            return response

        except CircuitOpenError as e:
            logger.error(
                'http_circuit_open',
                source=self.source,
                url=url,
                error=str(e),
            )
            return None

        except requests.exceptions.Timeout as e:
            logger.error(
                'http_timeout',
                source=self.source,
                url=url,
                error=str(e),
            )
            return None

        except requests.exceptions.ConnectionError as e:
            logger.error(
                'http_connection_error',
                source=self.source,
                url=url,
                error=str(e),
            )
            return None

        except Exception as e:
            logger.error(
                'http_unknown_error',
                source=self.source,
                url=url,
                error=str(e),
                error_type=type(e).__name__,
            )
            return None

    def _register_raw_row(self, row: dict) -> None:
        """원본 데이터 등록 (raw_archiver에서 사용)"""
        self.raw_rows.append(row)

    def _clear_raw_rows(self) -> None:
        """원본 데이터 초기화"""
        self.raw_rows.clear()