# Design

## Why Postgres for relational state and Redis for raw artifacts

Postgres owns everything that must be queried, constrained, or joined: task
sets, eval runs, run items, grades, and review items. Raw provider payloads
(request, raw response, judge output) live in Redis under
`artifact:{run_item_id}`, with Postgres storing only the pointer
(`run_item.artifact_key`). Reasons:

- **Cardinality vs value**: raw payloads are large (KB-MB), written once, read
  rarely (debugging, re-grading, review). Structured rows are small, updated
  constantly, and aggregated heavily. Blobs in the hot tables would bloat
  indexes and slow every scan.
- **Schema drift**: provider payload shapes change with provider/model
  versions. Storing them as JSONB would couple migrations and contracts to
  third-party formats; Redis treats them as opaque bytes.
- **Lifecycle**: artifacts are disposable; Redis TTLs expire them without
  destructive DELETEs against the transactional store.
- **Write-path isolation**: completion bursts hit Redis with a single SET per
  item instead of inflating Postgres WAL.

## Backpressure and DLQ semantics

- The `evals` queue uses `task_acks_late` with `worker_prefetch_multiplier=1`:
  a worker holds at most a handful of in-flight tasks, and a crashed worker's
  unacked task is re-queued instead of lost.
- Concurrency is bounded by `WORKER_CONCURRENCY` (default 4) — the queue
  itself provides backpressure; there is no unbounded prefetch.
- Transient failures (429/5xx/timeout) retry with exponential backoff +
  jitter up to `RETRY_MAX_ATTEMPTS`, incrementing `run_item.attempt_count`
  per attempt. Exhaustion marks the item `dead_lettered`, persists the error,
  and publishes a notice to `evals.dlq` for retention.
- Replay (`POST /v1/dead-letters/{id}/replay`) resets the item to `queued`
  and re-enqueues it. Cost rollup uses deltas and a `status='running'` guard
  on the finalizing UPDATE, so replays can never double-count cost.
- Permanent (non-retryable) failures fail immediately — retrying them wastes
  the attempt budget.

## Why human labels outrank judge scores

LLM judges are fallible and biased in ways that are invisible at scoring
time; the judge itself is a model under evaluation. A human reviewer's
verdict is the highest-quality signal the system can produce, so the
leaderboard's effective score per run item is:

```
resolved reviewer_label  >  llm_judge score  >  deterministic score
```

Humans only review items the pipeline flags (judge/deterministic
disagreement or low-confidence judge scores), so the label does not require
100% human coverage — it upgrades the signal where the machine signals are
weakest.

## Reliability and cost guards

- `MAX_SPEND_PER_RUN_USD` bounds per-run spend. When a worker's rollup pushes
  the running total over the limit, it aborts the run: `eval_run` is marked
  `failed` with a clear error and remaining queued items are failed with an
  abort message. Other in-flight workers check run status before executing.
- API keys are salted PBKDF2 hashes in Postgres with an indexed prefix for
  fast lookup; the raw key is shown exactly once at creation time.

## Known limits

- **Approximate spend guard**: the abort triggers on observed (projected)
  running cost, not an upfront estimate — an in-flight item can overshoot the
  limit by one completion. Token-accurate preflight would require per-task
  price models.
- **Single judge model**: the LLM judge uses one `JUDGE_MODEL_ID` globally;
  per-task judge selection is not supported.
- **No key revocation cache**: authentication verifies against Postgres per
  request. Deleting an API key takes effect immediately, but no in-memory
  caching means one PBKDF2 verification (and one indexed query) per request.
- **Leaderboard score precedence via SQL**: the COALESCE precedence assumes
  at most one grade per (run_item, grader) and one review_item per run_item,
  which the grading pipeline enforces; bypassing the pipeline could double
  rows.
- **Multiproc metrics**: with `PROMETHEUS_MULTIPROC_DIR`, dead processes
  (e.g. uvicorn --reload restarts) leave stale metric files; restart the
  service and clear the directory to reset.
- **Redis as the only broker**: broker, artifacts, and DLQ share one Redis;
  a Redis outage stalls both execution and artifact access (but not API
  reads that touch Postgres only).
- **Auth coverage**: health/metrics endpoints are intentionally unauthenticated;
  they should not be exposed publicly.
