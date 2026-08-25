"""
Bareun Pipeline: Code Triage Diagnostic
- Scans local Python files and classifies problem/clean/stable candidates.
- Heuristic only; final judgment requires actual code review.
- No network I/O. Free-tier safe.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Final


class Severity(str, Enum):
    WARN = 'warn'
    BLOCK = 'block'


@dataclass(frozen=True)
class Issue:
    severity: Severity
    code: str
    message: str
    masked: str | None = None


@dataclass
class FileReport:
    path: str
    lines: int = 0
    function_count: int = 0
    class_count: int = 0
    max_function_lines: int = 0
    issues: list[Issue] = field(default_factory=list)
    volatile_imports: set[str] = field(default_factory=set)
    parse_error: bool = False

    @property
    def problem(self) -> bool:
        return (
            self.parse_error
            or any(issue.severity is Severity.BLOCK for issue in self.issues)
            or len(self.issues) >= 4
        )

    @property
    def clean(self) -> bool:
        return not self.problem and not self.issues

    @property
    def stable_candidate(self) -> bool:
        return self.clean and self.lines <= 250 and not self.volatile_imports


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


class CodeTriage:
    MAX_FILE_BYTES: Final[int] = 512 * 1024
    MAX_FILE_LINES: Final[int] = 350
    MAX_FUNCTIONS: Final[int] = 25
    MAX_FUNCTION_LINES: Final[int] = 80
    MAX_CLASSES: Final[int] = 5

    EXCLUDE_DIRS: Final[frozenset[str]] = frozenset(
        {
            '.git',
            '.venv',
            'venv',
            '__pycache__',
            'node_modules',
            '.mypy_cache',
            '.pytest_cache',
            '.ruff_cache',
            'dist',
            'build',
        }
    )

    VOLATILE_IMPORTS: Final[frozenset[str]] = frozenset(
        {
            'requests',
            'httpx',
            'urllib',
            'supabase',
            'psycopg',
            'asyncpg',
            'sqlalchemy',
            'prefect',
            'transformers',
            'torch',
            'spacy',
            'ollama',
            'openai',
        }
    )

    SECRET_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
        (
            'supabase_jwt',
            re.compile(r'eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{10,}'),
        ),
        ('openai_key', re.compile(r'sk-[A-Za-z0-9]{16,}')),
        ('bearer', re.compile(r'Bearer\s+[A-Za-z0-9_\-\.=]{20,}', re.IGNORECASE)),
        (
            'env_secret',
            re.compile(
                r"(?i)^\s*(?:API_KEY|SECRET|TOKEN|PASSWORD|SERVICE_ROLE_KEY)\s*=\s*['\"]?[A-Za-z0-9_\-\.]{16,}['\"]?"
            ),
        ),
    )

    TODO_PATTERN: Final[re.Pattern[str]] = re.compile(r'(?i)\bTO' 'DO\b|\bFIXME\b|\bXXX\b')

    def __init__(self, root: Path, out: Path, max_files: int) -> None:
        self.root = root.resolve()
        self.out = out.resolve()
        self.max_files = max_files
        self.breaker = CircuitBreaker(max_failures=3)

    @staticmethod
    def _mask(raw: str) -> str:
        digest = hashlib.sha256(raw.encode('utf-8', errors='ignore')).hexdigest()[:12]
        return f'sha256:{digest}'

    def _iter_python_files(self) -> Iterable[Path]:
        for path in sorted(self.root.rglob('*.py')):
            if any(part in self.EXCLUDE_DIRS for part in path.parts):
                continue
            try:
                if path.is_file() and path.stat().st_size <= self.MAX_FILE_BYTES:
                    yield path
            except OSError:
                continue

    def _read_with_backoff(self, path: Path) -> str | None:
        delay = 0.05
        for attempt in range(3):
            try:
                text = path.read_text(encoding='utf-8', errors='ignore')
                self.breaker.record_success()
                return text
            except OSError:
                self.breaker.record_failure()
                if attempt == 2:
                    return None
                time.sleep(delay)
                delay *= 2.0
        return None

    def _scan_text(self, text: str) -> list[Issue]:
        issues: list[Issue] = []
        for name, pattern in self.SECRET_PATTERNS:
            for match in pattern.finditer(text):
                issues.append(
                    Issue(
                        Severity.BLOCK,
                        'secret',
                        f'possible {name}',
                        self._mask(match.group(0)),
                    )
                )
        if self.TODO_PATTERN.search(text):
            issues.append(Issue(Severity.WARN, 'todo', 'TODO/FIXME marker found'))
        return issues

    def _analyze_ast(self, text: str, report: FileReport) -> None:
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            report.parse_error = True
            report.issues.append(Issue(Severity.BLOCK, 'syntax', f'SyntaxError: {exc.msg}'))
            return

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                report.function_count += 1
                if node.end_lineno is not None:
                    length = node.end_lineno - node.lineno + 1
                    report.max_function_lines = max(report.max_function_lines, length)
            elif isinstance(node, ast.ClassDef):
                report.class_count += 1
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split('.')[0]
                    if top in self.VOLATILE_IMPORTS:
                        report.volatile_imports.add(top)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    top = node.module.split('.')[0]
                    if top in self.VOLATILE_IMPORTS:
                        report.volatile_imports.add(top)
            elif isinstance(node, ast.ExceptHandler):
                if node.type is None:
                    report.issues.append(
                        Issue(Severity.WARN, 'bare_except', 'bare except detected')
                    )
                elif isinstance(node.type, ast.Name) and node.type.id in {
                    'Exception',
                    'BaseException',
                }:
                    report.issues.append(
                        Issue(
                            Severity.WARN,
                            'broad_except',
                            f'broad except {node.type.id} detected',
                        )
                    )

    def _analyze_file(self, path: Path) -> FileReport:
        report = FileReport(path=path.relative_to(self.root).as_posix())
        text = self._read_with_backoff(path)
        if text is None:
            report.parse_error = True
            report.issues.append(Issue(Severity.BLOCK, 'io', 'unreadable file'))
            return report

        report.lines = len(text.splitlines())
        report.issues.extend(self._scan_text(text))
        self._analyze_ast(text, report)

        if report.lines > self.MAX_FILE_LINES:
            report.issues.append(
                Issue(
                    Severity.WARN, 'file_too_long', f'lines={report.lines} > {self.MAX_FILE_LINES}'
                )
            )
        if report.function_count > self.MAX_FUNCTIONS:
            report.issues.append(
                Issue(
                    Severity.WARN,
                    'too_many_functions',
                    f'functions={report.function_count} > {self.MAX_FUNCTIONS}',
                )
            )
        if report.max_function_lines > self.MAX_FUNCTION_LINES:
            report.issues.append(
                Issue(
                    Severity.WARN,
                    'long_function',
                    f'max_function_lines={report.max_function_lines} > {self.MAX_FUNCTION_LINES}',
                )
            )
        if report.class_count > self.MAX_CLASSES:
            report.issues.append(
                Issue(
                    Severity.WARN,
                    'too_many_classes',
                    f'classes={report.class_count} > {self.MAX_CLASSES}',
                )
            )
        return report

    def _split_suggestion(self, report: FileReport) -> str | None:
        reasons: list[str] = []
        if report.lines > self.MAX_FILE_LINES:
            reasons.append('large file')
        if report.function_count > self.MAX_FUNCTIONS:
            reasons.append('too many functions')
        if report.max_function_lines > self.MAX_FUNCTION_LINES:
            reasons.append('long function')
        if report.class_count > self.MAX_CLASSES:
            reasons.append('too many classes')
        if report.volatile_imports and report.lines > 250:
            reasons.append('volatile dependencies + size')
        if not reasons:
            return None
        return (
            f"{', '.join(reasons)}. "
            "Split by responsibility: adapters/clients, silver/transform, gold/repository, "
            "domain/models, io/checkpoint."
        )

    def _render_report(self, reports: list[FileReport]) -> str:
        problem = [report for report in reports if report.problem]
        needs_review = [report for report in reports if not report.problem and report.issues]
        clean = [report for report in reports if report.clean]
        stable = [report for report in reports if report.stable_candidate]
        splits = [
            (report, suggestion)
            for report in reports
            if (suggestion := self._split_suggestion(report))
        ]
        now = datetime.now(UTC).isoformat(timespec='seconds')

        lines = [
            '# Bareun Code Triage Report',
            f'- generated_at_utc: {now}',
            f'- root: {self.root}',
            f'- scanned_files: {len(reports)}',
            f'- problem_files: {len(problem)}',
            f'- needs_review_files: {len(needs_review)}',
            f'- clean_files: {len(clean)}',
            f'- stable_candidates: {len(stable)}',
            '',
            '## Recommended Folder Boundaries',
            '- adapters: external API/quota; bronze: raw ingest; silver: normalize/match/enrich',
            '- gold: repository/upsert/PostGIS; domain: models/rules; io: checkpoint/state',
            '- observability: logging/diagnostics; cli: command wiring only',
            '',
        ]

        lines.append('## Problem Files')
        if not problem:
            lines.append('- none')
        for report in problem:
            lines.append(
                f'- {report.path} | lines={report.lines} | funcs={report.function_count} '
                f'| classes={report.class_count} | issues={len(report.issues)}'
            )
            for issue in report.issues:
                masked = f' [{issue.masked}]' if issue.masked else ''
                lines.append(
                    f'  - {issue.severity.value.upper()} {issue.code}: {issue.message}{masked}'
                )
        lines.append('')

        lines.append('## Needs Review')
        if not needs_review:
            lines.append('- none')
        lines.extend(f'- {report.path} | issues={len(report.issues)}' for report in needs_review)
        lines.append('')

        lines.append('## Clean Files')
        if not clean:
            lines.append('- none')
        lines.extend(f'- {report.path}' for report in clean)
        lines.append('')

        lines.append('## Stable Candidates (likely low-change)')
        if not stable:
            lines.append('- none')
        lines.extend(f'- {report.path}' for report in stable)
        lines.append('')

        lines.append('## Split Suggestions')
        if not splits:
            lines.append('- none')
        for report, suggestion in splits:
            lines.append(f'- {report.path}: {suggestion}')
        return '\n'.join(lines)

    def build(self) -> int:
        reports: list[FileReport] = []
        for path in self._iter_python_files():
            if len(reports) >= self.max_files or not self.breaker.allow():
                break
            reports.append(self._analyze_file(path))

        self.out.parent.mkdir(parents=True, exist_ok=True)
        self.out.write_text(self._render_report(reports), encoding='utf-8')

        problem_count = sum(1 for report in reports if report.problem)
        clean_count = sum(1 for report in reports if report.clean)
        stable_count = sum(1 for report in reports if report.stable_candidate)
        print(
            f'scanned={len(reports)} problem={problem_count} '
            f'clean={clean_count} stable={stable_count} out={self.out}'
        )
        return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Triage Bareun Pipeline Python code for review.')
    parser.add_argument('--root', default='.', help='Repository root')
    parser.add_argument('--out', default='code_triage.md', help='Output report path')
    parser.add_argument('--max-files', type=int, default=200, help='Max files to scan')
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    triage = CodeTriage(root=Path(args.root), out=Path(args.out), max_files=args.max_files)
    return triage.build()


if __name__ == '__main__':
    raise SystemExit(main())
