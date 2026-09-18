import pytest
from app.graders.deterministic import (
    grade_exact_match,
    grade_regex,
    run_deterministic,
)
from app.graders.llm_judge import (
    JudgeParseError,
    JudgeVerdict,
    RubricCriterion,
    build_judge_prompt,
    extract_json,
    grade,
)
from app.models import GraderType, Task
from pydantic import ValidationError

from tests.helpers import ScriptedProvider, judge_json


class TestExactMatch:
    def test_default_normalizes_whitespace_and_case(self) -> None:
        result = grade_exact_match("Hello   World", "hello world")
        assert result.passed is True
        assert float(result.score) == 1.0

    def test_case_sensitive_config(self) -> None:
        result = grade_exact_match(
            "Hello",
            "hello",
            {"case_sensitive": True},
        )
        assert result.passed is False
        assert float(result.score) == 0.0

    def test_whitespace_normalization_disabled(self) -> None:
        result = grade_exact_match(
            "a  b",
            "a b",
            {"normalize_whitespace": False},
        )
        assert result.passed is False


class TestRegex:
    def test_search_by_default(self) -> None:
        result = grade_regex("the answer is 42", r"\d+")
        assert result.passed is True

    def test_fullmatch_config(self) -> None:
        result = grade_regex("42", r"\d+", {"fullmatch": True})
        assert result.passed is True
        result = grade_regex("the answer is 42", r"\d+", {"fullmatch": True})
        assert result.passed is False

    def test_ignore_case(self) -> None:
        result = grade_regex("HELLO", r"^hello$", {"ignore_case": True})
        assert result.passed is True


class TestRunDeterministic:
    def test_exact_match_requires_expected_output(self) -> None:
        task = Task(
            prompt="q",
            capability="c",
            grader_type=GraderType.exact_match,
            expected_output=None,
        )
        assert run_deterministic(task, "anything") is None

    def test_regex_uses_config_pattern(self) -> None:
        task = Task(
            prompt="q",
            capability="c",
            grader_type=GraderType.regex,
            grader_config={"pattern": r"\d+"},
        )
        result = run_deterministic(task, "42")
        assert result is not None and result.passed is True


class TestExtractJson:
    def test_plain_json(self) -> None:
        assert extract_json('{"a": 1}') == {"a": 1}

    def test_fenced_json(self) -> None:
        assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_embedded_json(self) -> None:
        assert extract_json('Here it is: {"a": 1}. Thanks!') == {"a": 1}

    def test_garbage_raises(self) -> None:
        with pytest.raises(JudgeParseError):
            extract_json("no json here")


class TestJudgeVerdict:
    def test_rejects_out_of_range_scores(self) -> None:
        with pytest.raises(ValidationError):
            JudgeVerdict.model_validate(
                {"rubric_scores": {"accuracy": 1.5}, "overall": 0.5, "rationale": "x"}
            )

    def test_rejects_out_of_range_overall(self) -> None:
        with pytest.raises(ValidationError):
            JudgeVerdict.model_validate(
                {"rubric_scores": {"accuracy": 0.5}, "overall": 2.0, "rationale": "x"}
            )


class TestGrade:
    RUBRIC = [
        RubricCriterion(name="accuracy", description="is it right", weight=1.0),
        RubricCriterion(name="clarity", description="is it clear", weight=1.0),
    ]

    async def test_parses_valid_verdict(self) -> None:
        provider = ScriptedProvider(judge_json(0.8, {"accuracy": 0.9, "clarity": 0.7}))
        verdict = await grade(
            provider,
            prompt="What is 2+2?",
            expected_output="4",
            output="4",
            rubric=self.RUBRIC,
            model_id="judge-model",
        )
        assert verdict.overall == 0.8
        assert verdict.rubric_scores == {"accuracy": 0.9, "clarity": 0.7}

    async def test_retries_once_on_parse_failure(self) -> None:
        provider = ScriptedProvider(
            ["garbage response", judge_json(0.75, {"accuracy": 0.75, "clarity": 0.75})]
        )
        verdict = await grade(
            provider,
            prompt="q",
            expected_output=None,
            output="a",
            rubric=self.RUBRIC,
            model_id="judge-model",
        )
        assert verdict.overall == 0.75
        assert provider.calls == 2

    async def test_fails_after_second_parse_failure(self) -> None:
        provider = ScriptedProvider(["garbage one", "garbage two"])
        with pytest.raises(JudgeParseError):
            await grade(
                provider,
                prompt="q",
                expected_output=None,
                output="a",
                rubric=self.RUBRIC,
                model_id="judge-model",
            )
        assert provider.calls == 2

    async def test_rejects_missing_rubric_keys(self) -> None:
        provider = ScriptedProvider(
            judge_json(0.9, {"accuracy": 0.9})  # missing "clarity"
        )
        with pytest.raises(JudgeParseError):
            await grade(
                provider,
                prompt="q",
                expected_output=None,
                output="a",
                rubric=self.RUBRIC,
                model_id="judge-model",
            )


class TestBuildJudgePrompt:
    def test_includes_criteria_and_expected(self) -> None:
        rubric = [RubricCriterion(name="accuracy", description="correctness", weight=1.0)]
        prompt = build_judge_prompt("2+2?", "4", "4", rubric)
        assert "accuracy" in prompt
        assert "Expected output: 4" in prompt
