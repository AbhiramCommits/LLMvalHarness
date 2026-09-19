# llm-eval-harness

An LLM evaluation harness: define task sets, run them against model endpoints
through Celery workers, grade outputs deterministically and/or with an LLM
judge, route disagreements to a human review queue, and rank models on a
leaderboard.

Stack: FastAPI + Pydantic v2, SQLAlchemy 2.0 (async) + Alembic, PostgreSQL 16,
Redis 7, Celery, Docker Compose, pytest (+ coverage), ruff, mypy.

## Architecture

```
                        +---------------------------------------------+
                        |                    API (FastAPI)             |
                        |  /v1/task-sets  /v1/eval-runs  /v1/models   |
                        |  /v1/review-queue  /v1/leaderboard          |
                        +----+----------------------+-----------------+
                             |                      |
                      reads/writes                 | publishes run items
                             |                      v
                     +-------+--------+   +---------------------+
                     | PostgreSQL 16  |   |       Redis 7       |
                     | task sets,     |<--+ artifacts:          |
                     | runs, items,   |   | artifact:{run_item} |
                     | grades, review |   | queues: evals, dlq  |
                     +-------+--------+   +----------+----------+
                             ^                       |
                             | results, grades       | tasks
                             |                       v
                     +-------+-----------------------+
                     |     Celery workers (evals)    |
                     |  retries, DLQ, cost rollup    |
                     +-------+-----------------------+
                             | complete(prompt, model_id)
                             v
                     +-------------------------------+
                     | Provider adapters             |
                     | openai | anthropic | fake     |
                     +-------------------------------+
                             |
                             v
                     +-------------------------------+
                     | Graders                      |
                     | exact_match | regex | judge  |
                     +-------------------------------+
                             |
              disagreement / low confidence
                             v
                     +-------------------------------+
                     | Review queue (human)          |
                     | claim -> resolve -> label     |
                     +-------------------------------+
                             |
                             v
                     +-------------------------------+
                     | Leaderboard (SQL aggregates)  |
                     | mean score, p95, cost, history|
                     +-------------------------------+
```

## Quickstart (zero API keys)

Everything below runs against `FakeProvider`, a deterministic no-key provider
used for local dev and tests.

```bash
docker compose up -d --build   # postgres + redis + api + worker
make migrate                   # apply alembic migrations
make load                      # creates a throwaway admin key, seeds 50x3, runs an eval
```

API endpoints require an API key (hashed in Postgres, roles admin/reviewer).
Create one for manual use:

```bash
docker compose exec api python scripts/create_api_key.py dev-admin admin
# prints the raw key exactly once
```

Then poke the API (host port 8002 by default):

```bash
curl -H "X-API-Key: <key>" localhost:8002/v1/task-sets
curl -H "X-API-Key: <key>" "localhost:8002/v1/leaderboard"
curl localhost:8002/healthz          # liveness, no auth
curl localhost:8002/readyz           # postgres + redis + broker, no auth
curl localhost:8002/metrics          # prometheus, no auth
```

To use a real provider, set `EVAL_PROVIDER=openai|anthropic` plus
`OPENAI_API_KEY` / `ANTHROPIC_API_KEY` in the environment before `docker
compose up` (see `.env.example`).

## API reference

| Method | Path | Description |
| --- | --- | --- |
| GET | `/healthz` | Liveness check (no auth) |
| GET | `/readyz` | Readiness: Postgres + Redis + broker (no auth) |
| GET | `/metrics` | Prometheus metrics (no auth) |
| POST | `/v1/api-keys` | Create API key (admin); raw key returned once |
| GET | `/v1/api-keys` | List API keys (admin) |
| DELETE | `/v1/api-keys/{id}` | Revoke API key (admin) |
| POST | `/v1/task-sets` | Create task set (admin) |
| GET | `/v1/task-sets` | List task sets (admin) |
| GET | `/v1/task-sets/{id}` | Get task set (admin) |
| POST | `/v1/task-sets/{id}/tasks` | Add task (prompt, capability, grader) (admin) |
| POST | `/v1/models` | Register model endpoint (admin) |
| GET | `/v1/models` | List model endpoints (admin) |
| GET | `/v1/models/{id}` | Get model endpoint (admin) |
| POST | `/v1/eval-runs` | Start run; fans out task x model items, enqueues to Celery, 202 (admin) |
| GET | `/v1/eval-runs/{id}` | Status + per-model aggregates (counts, mean latency, cost) (admin) |
| POST | `/v1/eval-runs/{id}/cancel` | Cancel run; workers check before executing (admin) |
| GET | `/v1/eval-runs/{id}/dead-letters` | List dead-lettered items (admin) |
| POST | `/v1/dead-letters/{run_item_id}/replay` | Requeue a dead-lettered item (idempotent cost rollup) (admin) |
| GET | `/v1/review-queue?status=&capability=&limit=` | Review items, oldest first, with prompt, model output, grades (admin/reviewer) |
| POST | `/v1/review-queue/{id}/claim` | Atomically claim an open review item (409 on conflict) (admin/reviewer) |
| POST | `/v1/review-queue/{id}/resolve` | Resolve with `{label: 0..1, notes}`; label is authoritative (admin/reviewer) |
| GET | `/v1/review-queue/stats` | Open/claimed/resolved counts, mean time-to-resolve (admin/reviewer) |
| GET | `/v1/leaderboard?task_set_id=&capability=` | Per-model mean score, pass rate, n, mean/p95 latency, cost (admin/reviewer) |
| GET | `/v1/leaderboard/history?model_name=` | Score per model_version over eval runs (regression view) (admin/reviewer) |
| GET | `/v1/leaderboard/capabilities?model_endpoint_id=` | Score breakdown by capability (admin/reviewer) |

Auth: API keys are salted PBKDF2 hashes in Postgres; reviewers can only hit
`/v1/review-queue*` and `/v1/leaderboard*`, admins everything.

## Execution pipeline

- A run item is executed by a Celery task on the `evals` queue
  (`acks_late`, prefetch 1). 429/5xx/timeouts retry with exponential backoff
  + jitter, max 5 attempts, incrementing `attempt_count`. Exhausted items are
  `dead_lettered` and published to `evals.dlq`.
- Cost is priced per 1M tokens (`app/pricing.py`) and rolled into `eval_run`
  with delta-based updates, so replays never double-count.
- `MAX_SPEND_PER_RUN_USD` aborts a run once projected spend exceeds the limit:
  the run is marked failed with a clear error and remaining queued items are
  failed.
- Raw provider payloads live in Redis under `artifact:{run_item_id}`;
  PostgreSQL stores only `artifact_key` (rationale in `app/artifacts.py`).
- Logs are JSON (structlog) with `request_id` and `eval_run_id` in context;
  Prometheus metrics at `/metrics`.

## Grading & review

- Deterministic graders (`exact_match` with whitespace/case normalization,
  `regex`) return 1.0/0.0.
- The LLM judge scores a rubric from `grader_config` and parses its verdict
  with Pydantic (`JudgeVerdict`). Parse failure retries once; a second failure
  records a failed grade (`score = NULL`) — never a silent 0.
- Tasks with both an expectation and a rubric run both graders.
- Disagreement (`|det - judge| > 0.5`) or a judge score inside the
  low-confidence band (0.4–0.6) opens a `review_item`. Thresholds are env
  configurable (`DISAGREEMENT_THRESHOLD`, `LOW_CONFIDENCE_MIN/MAX`).

## Leaderboard

All aggregations run in SQL (`GROUP BY`, `percentile_cont` for p95) — no
Python row loops. Effective score precedence per item:

```
human reviewer_label (resolved review)  >  llm_judge  >  deterministic
```

The query is served by the composite index `ix_run_item_task_id_status`
(`run_item(task_id, status)`, migration `0003`), used to filter succeeded
items when joining from `task`. At small scale PostgreSQL prefers a seq scan;
with `SET enable_seqscan = off` the plan uses the index:

```
 ->  Bitmap Heap Scan on run_item ri
       Recheck Cond: (status = 'succeeded'::run_item_status)
       ->  Bitmap Index Scan on ix_run_item_task_id_status
             Index Cond: (status = 'succeeded'::run_item_status)
```

Full `EXPLAIN (ANALYZE, BUFFERS)` of the leaderboard query on a 165-item
database (planner choice: seq scans, `Execution Time: 0.286 ms`):

```
 Sort  (cost=17.70..17.71 rows=5 width=195) (actual time=0.201..0.204 rows=1 loops=1)
   Sort Key: (round(avg(COALESCE(rv.reviewer_label, jg.score, dg.score)), 3)) DESC, me.name
   ->  GroupAggregate  (cost=15.33..16.57 rows=5 width=195)
         Group Key: me.id
         ->  Sort  (cost=15.33..15.42 rows=39 width=45)
               ->  Hash Join  (cost=6.11..14.30 rows=39 width=45)
                     Hash Cond: (ri.model_endpoint_id = me.id)
                     ->  Hash Left Join  (cost=5.00..12.99 rows=39 width=34)
                           Hash Cond: (ri.id = dg.run_item_id)
                           ->  Hash Left Join  (cost=3.96..11.80 rows=39 width=47)
                                 Hash Cond: (ri.id = jg.run_item_id)
                                 ->  Hash Left Join  (cost=2.93..10.61 rows=39 width=44)
                                       Hash Cond: (ri.id = rv.run_item_id)
                                       ->  Hash Join  (cost=1.90..9.42 rows=39 width=39)
                                             Hash Cond: (ri.task_id = t.id)
                                             ->  Seq Scan on run_item ri
                                                   Filter: (status = 'succeeded'::run_item_status)
                                             ->  Hash  ->  Seq Scan on task t
                                                         Filter: (task_set_id = $0)
 Planning Time: 1.404 ms
 Execution Time: 0.286 ms
```

## Benchmark

`make load` seeds 50 tasks x 3 models (150 run items) and runs them through
the real Celery pipeline against `FakeProvider` (4 worker processes, Docker
Desktop on an Apple Silicon Mac):

```
eval run finished with status=completed
wall clock: 2.49s
throughput: 60.3 items/s

per-model aggregates:
  fake-model-0: succeeded=50 failed=0 dead_lettered=0 mean_latency_ms=13.26
  fake-model-1: succeeded=50 failed=0 dead_lettered=0 mean_latency_ms=12.08
  fake-model-2: succeeded=50 failed=0 dead_lettered=0 mean_latency_ms=12.64
```

Throughput is bounded by the FakeProvider being instant — real providers will
dominate the wall clock with their own latency.

## Deployment

- Production compose: `docker-compose.prod.yml` (multi-stage image, pinned
  deps via `requirements.lock.txt`, healthchecks, no bind mounts).
- ECS Fargate task definitions + RDS/ElastiCache CloudFormation under
  `deploy/ecs/`; images push to ECR on every push to `main`
  (`.github/workflows/deploy.yml`).
- Step-by-step instructions and the env var table: `docs/DEPLOY.md`.
- Design rationale and known limits: `docs/DESIGN.md`.

## Development

```bash
make up          # build + start everything
make migrate     # alembic upgrade head
make test        # pytest (111 tests)
make coverage    # pytest + coverage, >=80% required on app/{graders,workers,api}
make typecheck   # mypy app tests
make lint        # ruff check + format check
make load        # seed_and_run benchmark script
```

CI (`.github/workflows/ci.yml`) runs ruff, mypy, and pytest (with coverage
gate) against PostgreSQL 16 and Redis 7 service containers.
