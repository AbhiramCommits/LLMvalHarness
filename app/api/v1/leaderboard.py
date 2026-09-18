"""Leaderboard endpoints.

All aggregations run in SQL (GROUP BY + percentile_cont), never Python loops.

Effective score precedence per run item:
    1. human reviewer_label (resolved review_item)
    2. llm_judge grade score
    3. deterministic grade score

The pipeline guarantees at most one grade per (run_item, grader) and at most
one review_item per run_item, so the LEFT JOINs below never fan out rows.
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.schemas.leaderboard import CapabilityRow, HistoryPoint, LeaderboardRow

router = APIRouter()

# One row per succeeded run_item with its effective score plus the dimensions
# needed by the three endpoints. Callers inject extra joins/filters.
_EFFECTIVE_BODY = """
    SELECT
        ri.model_endpoint_id,
        ri.eval_run_id,
        ri.latency_ms,
        ri.cost_usd,
        t.capability,
        COALESCE(rv.reviewer_label, jg.score, dg.score) AS score
    FROM run_item ri
    JOIN task t ON t.id = ri.task_id
{extra_joins}
    LEFT JOIN review_item rv
        ON rv.run_item_id = ri.id AND rv.status = 'resolved'
    LEFT JOIN grade jg
        ON jg.run_item_id = ri.id AND jg.grader = 'llm_judge' AND jg.score IS NOT NULL
    LEFT JOIN grade dg
        ON dg.run_item_id = ri.id AND dg.grader = 'deterministic' AND dg.score IS NOT NULL
    WHERE ri.status = 'succeeded'
{extra_filters}
"""

_LEADERBOARD_SQL = f"""
WITH effective AS (
{_EFFECTIVE_BODY}
)
SELECT
    me.id AS model_endpoint_id,
    me.name AS name,
    ROUND(AVG(e.score)::numeric, 3) AS mean_score,
    ROUND(AVG((e.score >= 0.5)::int)::numeric, 3) AS pass_rate,
    COUNT(*) AS n,
    ROUND(AVG(e.latency_ms)::numeric, 1) AS mean_latency_ms,
    ROUND(percentile_cont(0.95) WITHIN GROUP (ORDER BY e.latency_ms)::numeric, 1)
        AS p95_latency_ms,
    SUM(e.cost_usd) AS total_cost_usd,
    ROUND(SUM(e.cost_usd) * 1000 / COUNT(*), 6) AS cost_per_1k
FROM effective e
JOIN model_endpoint me ON me.id = e.model_endpoint_id
WHERE e.score IS NOT NULL
GROUP BY me.id, me.name
ORDER BY mean_score DESC, me.name
"""

_HISTORY_SQL = f"""
WITH effective AS (
{_EFFECTIVE_BODY}
)
SELECT
    me.model_version AS model_version,
    er.id AS eval_run_id,
    er.created_at AS evaluated_at,
    ROUND(AVG(e.score)::numeric, 3) AS mean_score,
    COUNT(*) AS n
FROM effective e
JOIN model_endpoint me ON me.id = e.model_endpoint_id
JOIN eval_run er ON er.id = e.eval_run_id
WHERE e.score IS NOT NULL
  AND me.name = :model_name
GROUP BY me.model_version, er.id, er.created_at
ORDER BY er.created_at, me.model_version
"""

_CAPABILITIES_SQL = f"""
WITH effective AS (
{_EFFECTIVE_BODY}
)
SELECT
    e.capability AS capability,
    ROUND(AVG(e.score)::numeric, 3) AS mean_score,
    ROUND(AVG((e.score >= 0.5)::int)::numeric, 3) AS pass_rate,
    COUNT(*) AS n
FROM effective e
WHERE e.score IS NOT NULL
  AND e.model_endpoint_id = CAST(:model_endpoint_id AS uuid)
GROUP BY e.capability
ORDER BY e.capability
"""


@router.get("", response_model=list[LeaderboardRow])
async def get_leaderboard(
    task_set_id: UUID | None = None,
    capability: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[LeaderboardRow]:
    filters, params = _task_filters(task_set_id, capability)
    sql = _LEADERBOARD_SQL.format(extra_joins="", extra_filters=filters)
    rows = (await session.execute(text(sql), params)).all()
    return [
        LeaderboardRow(
            model_endpoint_id=row._mapping["model_endpoint_id"],
            name=row._mapping["name"],
            mean_score=float(row._mapping["mean_score"]),
            pass_rate=float(row._mapping["pass_rate"]),
            n=int(row._mapping["n"]),
            mean_latency_ms=_optional_float(row._mapping["mean_latency_ms"]),
            p95_latency_ms=_optional_float(row._mapping["p95_latency_ms"]),
            total_cost_usd=float(row._mapping["total_cost_usd"]),
            cost_per_1k=float(row._mapping["cost_per_1k"]),
        )
        for row in rows
    ]


@router.get("/history", response_model=list[HistoryPoint])
async def get_leaderboard_history(
    model_name: str = Query(min_length=1),
    session: AsyncSession = Depends(get_session),
) -> list[HistoryPoint]:
    sql = _HISTORY_SQL.format(extra_joins="", extra_filters="")
    rows = (
        await session.execute(
            text(sql),
            {"model_name": model_name},
        )
    ).all()
    return [
        HistoryPoint(
            model_version=row._mapping["model_version"],
            eval_run_id=row._mapping["eval_run_id"],
            evaluated_at=row._mapping["evaluated_at"],
            mean_score=float(row._mapping["mean_score"]),
            n=int(row._mapping["n"]),
        )
        for row in rows
    ]


@router.get("/capabilities", response_model=list[CapabilityRow])
async def get_capability_breakdown(
    model_endpoint_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> list[CapabilityRow]:
    sql = _CAPABILITIES_SQL.format(extra_joins="", extra_filters="")
    rows = (
        await session.execute(
            text(sql),
            {"model_endpoint_id": str(model_endpoint_id)},
        )
    ).all()
    return [
        CapabilityRow(
            capability=row._mapping["capability"],
            mean_score=float(row._mapping["mean_score"]),
            pass_rate=float(row._mapping["pass_rate"]),
            n=int(row._mapping["n"]),
        )
        for row in rows
    ]


def _task_filters(
    task_set_id: UUID | None,
    capability: str | None,
) -> tuple[str, dict[str, Any]]:
    filters = ""
    params: dict[str, Any] = {}
    if task_set_id is not None:
        filters += "    AND t.task_set_id = CAST(:task_set_id AS uuid)\n"
        params["task_set_id"] = str(task_set_id)
    if capability:
        filters += "    AND t.capability = :capability\n"
        params["capability"] = capability
    return filters, params


def _optional_float(value: Any) -> float | None:
    return float(value) if value is not None else None
