"""Deterministic graders: exact match and regex.

Both return a score of exactly 1.0 (pass) or 0.0 (fail); normalization is
driven by the task's ``grader_config``.
"""

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.models import Task
from app.models.enums import GraderType

_PASS = Decimal("1.0")
_FAIL = Decimal("0.0")


@dataclass(frozen=True)
class DeterministicGrade:
    score: Decimal
    passed: bool
    rationale: str


def _normalize_whitespace(text: str) -> str:
    return " ".join(text.split())


def grade_exact_match(
    actual: str,
    expected: str,
    config: dict[str, Any] | None = None,
) -> DeterministicGrade:
    cfg = config or {}
    if cfg.get("normalize_whitespace", True):
        actual, expected = _normalize_whitespace(actual), _normalize_whitespace(expected)
    if not cfg.get("case_sensitive", False):
        actual, expected = actual.lower(), expected.lower()
    matched = actual == expected
    return DeterministicGrade(
        score=_PASS if matched else _FAIL,
        passed=matched,
        rationale="exact match",
    )


def grade_regex(
    actual: str,
    pattern: str,
    config: dict[str, Any] | None = None,
) -> DeterministicGrade:
    cfg = config or {}
    flags = re.IGNORECASE if cfg.get("ignore_case", False) else 0
    if cfg.get("fullmatch", False):
        matched = re.fullmatch(pattern, actual, flags) is not None
    else:
        matched = re.search(pattern, actual, flags) is not None
    return DeterministicGrade(
        score=_PASS if matched else _FAIL,
        passed=matched,
        rationale=f"regex {pattern}",
    )


def run_deterministic(task: Task, actual: str) -> DeterministicGrade | None:
    """Run the deterministic grader for a task, or None if it is unconfigured."""
    cfg = task.grader_config or {}
    if task.grader_type == GraderType.exact_match:
        if task.expected_output is None:
            return None
        return grade_exact_match(actual, task.expected_output, cfg)
    if task.grader_type == GraderType.regex:
        pattern = cfg.get("pattern") or task.expected_output
        if not pattern:
            return None
        return grade_regex(actual, pattern, cfg)
    return None
