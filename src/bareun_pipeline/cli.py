"""
CLI 엔트리포인트 (Click 기반)
===============================
- bronze-collect: Bronze Layer 수집
- silver-process: Silver Layer 정제 (Phase 3에서 구현)
- gold-export: Gold Layer 내보내기 (Phase 4에서 구현)
- quota-status: 쿼터 사용 현황
- reset-checkpoint: 체크포인트 초기화

사용법:
    bareun-pipeline bronze-collect --province 제주특별자치도
    bareun-pipeline bronze-collect  # 전국 수집
    bareun-pipeline quota-status
"""

from __future__ import annotations

import logging

import click

from .observability.logging import get_logger, setup_logging
from .settings import get_settings


@click.group()
@click.option('--log-level', default='INFO', help='로그 레벨 (DEBUG/INFO/WARNING/ERROR)')
@click.option('--log-json/--log-console', default=True, help='JSON 포맷 로그 출력')
@click.pass_context
def cli(ctx: click.Context, log_level: str, log_json: bool) -> None:
    """바른인증식당 데이터 파이프라인 CLI"""
    ctx.ensure_object(dict)

    # 로깅 설정
    level = getattr(logging, log_level.upper(), logging.INFO)
    setup_logging(level=level, json_output=log_json)

    # 설정 로드
    settings = get_settings()
    ctx.obj['settings'] = settings


@cli.command()
@click.option('--province', '-p', default=None, help='시도명 (없으면 전국)')
@click.option('--force', '-f', is_flag=True, help='체크포인트 무시하고 강제 수집')
@click.pass_context
def bronze_collect(ctx: click.Context, province: str | None, force: bool) -> None:
    """Bronze Layer: 공공데이터 수집"""
    from .bronze.collect import BronzeCollector

    logger = get_logger(__name__)
    settings = ctx.obj['settings']

    # 디렉토리 생성
    settings.ensure_directories()

    collector = BronzeCollector()

    if province:
        # 단일 시도 수집
        result = collector.collect_province(province, force=force)
        logger.info('bronze_collect_complete', result=result)
    else:
        # 전국 수집
        result = collector.collect_nationwide(force=force)
        logger.info('bronze_collect_complete', result=result)


@cli.command()
@click.option('--province', '-p', default=None, help='시도명 (없으면 전국)')
@click.pass_context
def silver_process(ctx: click.Context, province: str | None) -> None:
    """Silver Layer: 데이터 정제 (Phase 3에서 구현)"""
    click.echo('Silver Layer는 Phase 3에서 구현됩니다.')


@cli.command()
@click.option('--province', '-p', default=None, help='시도명 (없으면 전국)')
@click.pass_context
def gold_export(ctx: click.Context, province: str | None) -> None:
    """Gold Layer: Supabase 적재 (Phase 4에서 구현)"""
    click.echo('Gold Layer는 Phase 4에서 구현됩니다.')


@cli.command()
def quota_status() -> None:
    """쿼터 사용 현황 출력"""
    from .observability.quota import QuotaManager

    qm = QuotaManager.get_instance()
    click.echo(f'쿼터 사용 현황: {qm.summary()}')


@cli.command()
@click.option('--province', '-p', required=True, help='시도명')
def reset_checkpoint(province: str) -> None:
    """체크포인트 초기화 (재수집용)"""
    from .io.checkpoint import CheckpointManager
    from .settings import get_settings

    settings = get_settings()
    cp = CheckpointManager(settings.checkpoint_dir)
    cp.reset(province)
    click.echo(f'{province} 체크포인트 초기화 완료')


@cli.command()
def reset_all_checkpoints() -> None:
    """모든 체크포인트 초기화"""
    from .io.checkpoint import CheckpointManager
    from .settings import get_settings

    settings = get_settings()
    cp = CheckpointManager(settings.checkpoint_dir)
    cp.reset_all()
    click.echo('모든 체크포인트 초기화 완료')


def main() -> None:
    """CLI 진입점"""
    cli(obj={})


if __name__ == '__main__':
    main()
