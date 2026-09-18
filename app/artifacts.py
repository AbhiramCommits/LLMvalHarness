"""Raw run artifacts stored in Redis, referenced from Postgres.

Storage split rationale
-----------------------
Postgres only stores structured, queryable columns. Each ``run_item`` row keeps
an ``artifact_key`` (e.g. ``artifact:<run_item_id>``) that points at the raw
payload in Redis. The raw payload is a JSON blob shaped like::

    {
        "request": {...},       # exact provider request (prompt, params)
        "raw_response": {...},  # unprocessed provider response
        "judge_raw": {...}      # raw output of the grader / LLM judge
    }

Why not store these blobs in Postgres?

1. **Cardinality vs. value**: raw provider payloads are large (tens of KB to
   several MB), written exactly once, and read rarely — only during debugging,
   re-grading, or dispute review. Structured run metadata (status, tokens,
   cost, latency) is small, frequently updated, and heavily queried. Keeping
   the blobs in the hot relational tables would bloat indexes, extend
   autovacuum runtimes, and slow every ``run_item`` scan.

2. **Schema stability**: every provider (openai/anthropic/local) returns a
   different payload shape, and those shapes drift with provider/model
   versions. Posting them as JSONB would tie migrations and API contracts to
   third-party formats we do not control. In Redis the blob is opaque bytes;
   consumers (replay, re-grade, review tooling) parse it with version-tagged
   logic instead of database constraints.

3. **Lifecycle**: artifacts are disposable by design. Redis gives us cheap TTL
   expiry and eviction policies for old runs without running destructive
   ``DELETE`` jobs against the transactional store. Postgres only needs to
   keep the small, immutable pointer.

4. **Write path isolation**: bursts of artifact writes (e.g. thousands of
   run items completing at once) hit Redis, which absorbs them with a single
   SET per item, instead of inflating Postgres WAL volume.
"""

import asyncio
import json
from typing import Any
from uuid import UUID

from redis.asyncio import Redis

from app.config import get_settings

_redis_client: Redis | None = None
_redis_loop: asyncio.AbstractEventLoop | None = None


def get_redis() -> Redis:
    """Return a Redis client bound to the current event loop.

    redis-py's asyncio connection pool is not loop-safe, so a client cached on
    one loop cannot be reused from another (e.g. a fresh ``asyncio.run`` in a
    script or test). We rebuild the client whenever the running loop changes.
    """
    global _redis_client, _redis_loop
    loop = asyncio.get_running_loop()
    if _redis_client is None or _redis_loop is not loop:
        _redis_client = Redis.from_url(get_settings().redis_url, decode_responses=True)
        _redis_loop = loop
    return _redis_client


def build_artifact_key(run_item_id: UUID | str) -> str:
    return f"artifact:{run_item_id}"


async def set_artifact(
    run_item_id: UUID | str,
    artifact: dict[str, Any],
    ttl_seconds: int | None = None,
) -> str:
    """Store a raw artifact blob under ``artifact:<run_item_id>`` and return the key."""
    key = build_artifact_key(run_item_id)
    await get_redis().set(key, json.dumps(artifact), ex=ttl_seconds)
    return key


async def get_artifact(run_item_id: UUID | str) -> dict[str, Any] | None:
    raw = await get_redis().get(build_artifact_key(run_item_id))
    if raw is None:
        return None
    return json.loads(raw)


async def delete_artifact(run_item_id: UUID | str) -> None:
    await get_redis().delete(build_artifact_key(run_item_id))
