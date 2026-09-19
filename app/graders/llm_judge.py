"""Rubric-scored LLM judge.

Builds a judge prompt from the task's rubric criteria, calls a provider, and
parses the structured JSON verdict with Pydantic. Parse failures are retried
once; a second failure raises :class:`JudgeParseError` so the caller records a
*failed* grade instead of silently scoring 0.
"""

import json
import re
from typing import Any

import structlog
from pydantic import BaseModel, Field, ValidationError, model_validator

from app.providers import Provider

logger = structlog.get_logger(__name__)


class RubricCriterion(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""
    weight: float = Field(gt=0.0)


class JudgeVerdict(BaseModel):
    rubric_scores: dict[str, float]
    overall: float = Field(ge=0.0, le=1.0)
    rationale: str

    @model_validator(mode="after")
    def _validate_scores(self) -> "JudgeVerdict":
        if any(not 0.0 <= score <= 1.0 for score in self.rubric_scores.values()):
            raise ValueError("rubric_scores must be within [0, 1]")
        return self


class JudgeParseError(Exception):
    """The judge response could not be parsed into a JudgeVerdict."""


def build_judge_prompt(
    prompt: str,
    expected_output: str | None,
    output: str,
    rubric: list[RubricCriterion],
) -> str:
    total_weight = sum(criterion.weight for criterion in rubric)
    criteria = "\n".join(
        f"- {c.name} (weight {c.weight}/{total_weight:.2f}): {c.description}" for c in rubric
    )
    expected_line = f"Expected output: {expected_output}\n" if expected_output else ""
    names = ", ".join(c.name for c in rubric)
    return f"""You are a strict eval judge. Score the model answer against each rubric criterion.

Task prompt:
{prompt}

{expected_line}Model output:
{output}

Rubric:
{criteria}

Return ONLY a JSON object with this exact shape:
{{"rubric_scores": {{"<criterion>": <0.0-1.0 score>}},
 "overall": <weighted mean of rubric_scores>,
 "rationale": "<one sentence>"}}

Criterion names: {names}"""


def extract_json(text: str) -> dict[str, Any]:
    """Extract a JSON object from provider text (fenced or embedded)."""
    stripped = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", stripped, re.DOTALL)
    if fence:
        stripped = fence.group(1).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", stripped, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise JudgeParseError(f"no parseable JSON object found: {exc}") from exc
    raise JudgeParseError("no JSON object found in judge response")


def _validate_rubric_keys(verdict: JudgeVerdict, rubric: list[RubricCriterion]) -> None:
    expected = {criterion.name for criterion in rubric}
    actual = set(verdict.rubric_scores)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise JudgeParseError(f"rubric_scores keys mismatch: missing={missing}, extra={extra}")


async def grade(
    provider: Provider,
    prompt: str,
    expected_output: str | None,
    output: str,
    rubric: list[RubricCriterion],
    model_id: str,
) -> JudgeVerdict:
    """Run the rubric judge, retrying the provider call once on parse failure."""
    judge_prompt = build_judge_prompt(prompt, expected_output, output, rubric)
    last_error: Exception | None = None
    for attempt in range(1, 3):
        result = await provider.complete(judge_prompt, model_id)
        try:
            data = extract_json(result.text)
            verdict = JudgeVerdict.model_validate(data)
            _validate_rubric_keys(verdict, rubric)
            return verdict
        except (JudgeParseError, ValidationError) as exc:
            last_error = exc
            logger.warning("judge parse failure (attempt %d/2): %s", attempt, exc)
            judge_prompt = (
                f"{judge_prompt}\n\nYour previous response was not parseable as the "
                f"required JSON ({exc}). Respond with ONLY the JSON object."
            )
    raise JudgeParseError(f"judge verdict unparseable after 2 attempts: {last_error}")
