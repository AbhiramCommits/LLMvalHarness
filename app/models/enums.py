import enum
from typing import Any


def enum_values(members: Any) -> list[str]:
    """Passed as ``values_callable`` so DB stores enum values, not names."""
    return [member.value for member in members]


class GraderType(enum.StrEnum):
    exact_match = "exact_match"
    regex = "regex"
    llm_judge = "llm_judge"


class Provider(enum.StrEnum):
    openai = "openai"
    anthropic = "anthropic"
    local = "local"


class EvalRunStatus(enum.StrEnum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class RunItemStatus(enum.StrEnum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    dead_lettered = "dead_lettered"


class GraderKind(enum.StrEnum):
    deterministic = "deterministic"
    llm_judge = "llm_judge"


class ReviewReason(enum.StrEnum):
    judge_disagreement = "judge_disagreement"
    low_confidence = "low_confidence"


class ReviewStatus(enum.StrEnum):
    open = "open"
    claimed = "claimed"
    resolved = "resolved"


class ApiKeyRole(enum.StrEnum):
    admin = "admin"
    reviewer = "reviewer"
