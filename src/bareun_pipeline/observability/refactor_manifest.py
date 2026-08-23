"""
Bareun Pipeline: Refactor Manifest
- Prioritized refactor plan based on 2026-08-23 triage report.
- No network I/O. Free-tier safe.
"""
from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import IntEnum
from pathlib import Path
from typing import Final


class Priority(IntEnum):
    P0 = 0
    P1 = 1
    P2 = 2


@dataclass(frozen=True)
class RefactorTarget:
    path: str
    priority: Priority
    target_package: str
    actions: tuple[str, ...]


class CircuitBreaker:
    def __init__(self, max_failures: int = 3) -> None:
        self.max_failures = max_failures
        self.failures = 0
        self.open = False

    def record_success(self) -> None:
        self.failures = 0
        self.open = False

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= self.max_failures:
            self.open = True

    def allow(self) -> bool:
        return not self.open


class RefactorManifest:
    TARGETS: Final[tuple[RefactorTarget, ...]] = (
        RefactorTarget(
            "src/bareun_pipeline/silver/enrich.py",
            Priority.P0,
            "src/bareun_pipeline/silver/enrich/",
            (
                "Split menu/photo/hours/reservation/contact enrichers",
                "Replace broad except with typed EnrichmentError and Result",
                "Move external detail calls behind adapter gateway",
                "Extract pure transform functions",
            ),
        ),
        RefactorTarget(
            "src/bareun_pipeline/adapters/base.py",
            Priority.P1,
            "src/bareun_pipeline/adapters/base/",
            (
                "Split http_client, retry, quota, paging, errors",
                "Separate response parsing from transport",
                "Replace broad except with typed AdapterError",
            ),
        ),
        RefactorTarget(
            "src/bareun_pipeline/adapters/tourist_info.py",
            Priority.P1,
            "src/bareun_pipeline/adapters/tourist_info/",
            (
                "Split client, detail_client, mapper, models",
                "Isolate daily quota checkpoint usage",
                "Replace broad except with typed TourApiError",
            ),
        ),
        RefactorTarget(
            "src/bareun_pipeline/adapters/food_safety.py",
            Priority.P1,
            "src/bareun_pipeline/adapters/food_safety/",
            (
                "Split client, mapper, validators",
                "Replace broad except with typed FoodSafetyError",
                "Extract pagination and parsing",
            ),
        ),
        RefactorTarget(
            "src/bareun_pipeline/adapters/general_restaurant.py",
            Priority.P1,
            "src/bareun_pipeline/adapters/general_restaurant/",
            (
                "Split client, status_checker, mapper",
                "Replace broad except with typed RestaurantError",
                "Extract long batch query function",
            ),
        ),
        RefactorTarget(
            "src/bareun_pipeline/bronze/collect.py",
            Priority.P1,
            "src/bareun_pipeline/bronze/orchestration/",
            (
                "Separate orchestration, adapter invocation, persistence",
                "Replace broad except with typed BronzeError",
                "Extract checkpoint-aware collect steps",
            ),
        ),
        RefactorTarget(
            "src/bareun_pipeline/gold/supabase_loader.py",
            Priority.P1,
            "src/bareun_pipeline/gold/repository/",
            (
                "Separate repository client, upsert, mapper, retry",
                "Replace broad except with typed GoldError",
                "Make batch upsert idempotent and observable",
            ),
        ),
        RefactorTarget(
            "src/bareun_pipeline/silver/menu_extract.py",
            Priority.P1,
            "src/bareun_pipeline/silver/enrich/menu/",
            (
                "Move into menu enrichment package",
                "Replace broad except with typed MenuExtractError",
                "Separate parser, normalizer, validator",
            ),
        ),
    )

    NEEDS_REVIEW: Final[tuple[str, ...]] = (
        "src/bareun_pipeline/adapters/low_sodium.py",
        "src/bareun_pipeline/adapters/safe_restaurant.py",
        "src/bareun_pipeline/bronze/raw_archiver.py",
        "src/bareun_pipeline/bronze/tourist_checkpoint.py",
        "src/bareun_pipeline/domain/record.py",
        "src/bareun_pipeline/gold/ingest_to_supabase.py",
        "src/bareun_pipeline/gold/transform.py",
        "src/bareun_pipeline/nlp/llm/verifier.py",
        "src/bareun_pipeline/nlp/ner/pipeline.py",
        "src/bareun_pipeline/nlp/ner/predictor.py",
        "src/bareun_pipeline/observability/code_triage.py",
        "src/bareun_pipeline/silver/merge.py",
        "src/bareun_pipeline/training/trainer.py",
        "src/scripts/e2e_sample_test.py",
        "src/scripts/probe_tourapi_key.py",
        "src/scripts/test_adapters.py",
        "src/scripts/test_coordinate_matching.py",
        "src/scripts/test_matching_radius.py",
        "src/scripts/test_single_province.py",
    )

    STABLE_PREFIXES: Final[tuple[str, ...]] = (
        "src/bareun_pipeline/domain/",
        "src/bareun_pipeline/io/",
        "src/bareun_pipeline/observability/",
        "src/tests/",
    )

    STABLE_FILES: Final[tuple[str, ...]] = (
        "src/bareun_pipeline/cli.py",
        "src/bareun_pipeline/settings.py",
        "src/bareun_pipeline/gold/export.py",
        "src/bareun_pipeline/silver/normalize.py",
        "src/bareun_pipeline/nlp/hybrid.py",
        "src/flows/menu_enrich_flow.py",
        "src/flows/national_pipeline.py",
    )

    FOLDER_RULES: Final[tuple[str, ...]] = (
        "- adapters/: external API clients, quota, retry, paging only",
        "- bronze/: raw ingest orchestration and NDJSON persistence only",
        "- silver/: normalization, matching, enrichment, classification only",
        "- gold/: repository, upsert, PostGIS loader, export only",
        "- domain/: pure models, constants, rules; no I/O",
        "- io/: checkpoint and local state only",
        "- observability/: logging, quota, diagnostics only",
        "- cli/: command wiring only; no business logic",
    )

    def render(self) -> str:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        lines = [
            "# Bareun Refactor Plan",
            f"- generated_at_utc: {now}",
            f"- problem_targets: {len(self.TARGETS)}",
            f"- needs_review_files: {len(self.NEEDS_REVIEW)}",
            "",
        ]

        for priority in Priority:
            group = [target for target in self.TARGETS if target.priority == priority]
            lines.append(f"## {priority.name} Targets")
            if not group:
                lines.append("- none")
            for target in group:
                lines.append(f"- {target.path} -> {target.target_package}")
                for action in target.actions:
                    lines.append(f"  - {action}")
            lines.append("")

        lines.append("## Needs Review")
        if not self.NEEDS_REVIEW:
            lines.append("- none")
        lines.extend(f"- {path}" for path in self.NEEDS_REVIEW)
        lines.append("")

        lines.append("## Stable Candidates")
        lines.append("- Primary stable list: use Stable Candidates from code_triage.md")
        lines.append("- Low-change anchors:")
        lines.extend(f"- prefix: {path}" for path in self.STABLE_PREFIXES)
        lines.extend(f"- {path}" for path in self.STABLE_FILES)
        lines.append("")

        lines.append("## Folder Rules")
        lines.extend(self.FOLDER_RULES)
        return "\n".join(lines)

    def write(self, out: Path) -> None:
        text = self.render()
        breaker = CircuitBreaker(max_failures=3)
        delay = 0.05

        for attempt in range(3):
            if not breaker.allow():
                break
            try:
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(text, encoding="utf-8")
                breaker.record_success()
                return
            except OSError:
                breaker.record_failure()
                if attempt == 2:
                    raise
                time.sleep(delay)
                delay *= 2.0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Bareun refactor manifest output.")
    parser.add_argument("--out", default="refactor_plan.md", help="Output markdown path")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    manifest = RefactorManifest()
    out = Path(args.out)

    try:
        manifest.write(out)
    except OSError as exc:
        print(f"failed to write refactor plan: {exc}")
        return 1

    print(f"refactor plan written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())