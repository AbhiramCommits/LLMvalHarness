"""Prometheus metrics.

Metrics are incremented wherever the event is observed. In production the API
and workers run with ``PROMETHEUS_MULTIPROC_DIR`` set so each process writes
its own metrics file, and /metrics aggregates them via MultiProcessCollector.

- counters: eval_items_total{status,model}, dead_letters_total,
  review_items_total{reason}
- histograms: provider_latency_seconds{provider}, grade_duration_seconds
- gauge: queue_depth (evals queue length, sampled at scrape time)
"""

import os
from collections.abc import Iterator

from prometheus_client import REGISTRY, CollectorRegistry, Counter, Histogram, generate_latest
from prometheus_client.core import GaugeMetricFamily, Metric
from prometheus_client.multiprocess import MultiProcessCollector
from prometheus_client.registry import Collector
from redis import Redis as SyncRedis

from app.config import get_settings

EVAL_ITEMS = Counter(
    "eval_items_total",
    "Run items by terminal status and model endpoint name",
    ["status", "model"],
)
DEAD_LETTERS = Counter(
    "dead_letters_total",
    "Run items dead-lettered after exhausting retries",
)
REVIEW_ITEMS = Counter(
    "review_items_total",
    "Review items opened, by reason",
    ["reason"],
)
PROVIDER_LATENCY = Histogram(
    "provider_latency_seconds",
    "Provider completion latency in seconds",
    ["provider"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0),
)
GRADE_DURATION = Histogram(
    "grade_duration_seconds",
    "LLM judge grading duration in seconds",
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0),
)


def _queue_depth() -> float:
    client = SyncRedis.from_url(get_settings().redis_url, socket_timeout=2)
    try:
        return float(client.llen("evals"))
    except Exception:
        return 0.0
    finally:
        client.close()


class QueueDepthCollector(Collector):
    """Gauge sampled live from Redis at scrape time (works in multiproc mode)."""

    def collect(self) -> Iterator[Metric]:
        metric = GaugeMetricFamily(
            "queue_depth",
            "Pending items in the evals queue",
            value=_queue_depth(),
        )
        yield metric


REGISTRY.register(QueueDepthCollector())


def metrics_registry() -> CollectorRegistry:
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        registry = CollectorRegistry()
        MultiProcessCollector(registry)
        registry.register(QueueDepthCollector())
        return registry
    return REGISTRY


def render_metrics() -> bytes:
    return generate_latest(metrics_registry())
