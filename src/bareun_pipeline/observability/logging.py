"""
구조화 로깅 (structlog 기반)
================================
- JSON 포맷으로 로그 출력 (Datadog/Loki 호환)
- run_id, province, source 등 컨텍스트 자동 주입
- 로그 레벨 동적 설정

설계 원칙:
- 모든 로그는 구조화 (key-value)
- 컨텍스트 바인딩으로 자동 주입
- JSON 포맷으로 기계 판독 가능
"""

from __future__ import annotations

import logging

import structlog


def setup_logging(
    level: int = logging.INFO,
    json_output: bool = True,
) -> structlog.BoundLogger:
    """
    구조화 로거 설정

    Args:
        level: 로그 레벨 (logging.INFO 등)
        json_output: True=JSON 포맷, False=콘솔 친화적 포맷

    Returns:
        설정된 BoundLogger
    """
    processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
        structlog.processors.TimeStamper(fmt='iso'),
    ]

    if json_output:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    return structlog.get_logger()


def get_logger(name: str | None = None) -> structlog.BoundLogger:
    """
    로거 인스턴스 반환

    Args:
        name: 로거 이름 (보통 __name__)

    Returns:
        BoundLogger 인스턴스
    """
    return structlog.get_logger(name)


def bind_context(**kwargs) -> None:
    """
    로그 컨텍스트 바인딩 (현재 스레드/코루틴에 적용)

    Args:
        **kwargs: 컨텍스트 키-값 쌍

    사용법:
        bind_context(run_id='abc123', province='제주특별자치도')
        logger.info('collect_start')  # run_id, province 자동 포함
    """
    structlog.contextvars.bind_contextvars(**kwargs)


def unbind_context(*keys: str) -> None:
    """
    로그 컨텍스트 언바인딩

    Args:
        *keys: 제거할 컨텍스트 키
    """
    structlog.contextvars.unbind_contextvars(*keys)


def clear_context() -> None:
    """모든 로그 컨텍스트 초기화"""
    structlog.contextvars.clear_contextvars()
