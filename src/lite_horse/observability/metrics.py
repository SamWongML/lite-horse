"""CloudWatch Embedded Metric Format (EMF) helper.

Why EMF? It piggybacks on the log stream — each metric is one JSON line
on stdout. The CloudWatch agent attached to the ECS task parses those
lines and turns them into CloudWatch metrics. No extra SDK round-trip,
no metric-push pipeline to keep alive, and the same line is also
searchable in CloudWatch Logs.

Spec: https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch_Embedded_Metric_Format_Specification.html
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import IO, Any

DEFAULT_NAMESPACE = "litehorse"


def emit_metric(
    name: str,
    value: float,
    *,
    unit: str = "Count",
    dimensions: dict[str, str] | None = None,
    namespace: str = DEFAULT_NAMESPACE,
    extra: dict[str, Any] | None = None,
    stream: IO[str] | None = None,
) -> None:
    """Write one EMF JSON line for metric ``name`` with ``value``.

    ``dimensions`` is the optional CloudWatch dimension set; values must
    be strings. ``extra`` lets the caller attach searchable fields that
    aren't promoted to metrics (e.g. ``user_id`` for log search).
    """
    dims = dict(dimensions or {})
    payload: dict[str, Any] = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": namespace,
                    "Dimensions": [list(dims.keys())] if dims else [[]],
                    "Metrics": [{"Name": name, "Unit": unit}],
                }
            ],
        },
        name: value,
        "service": _service_name(),
        "env": os.environ.get("LITEHORSE_ENV", "local"),
        **dims,
    }
    if extra:
        payload.update(extra)

    out = stream if stream is not None else sys.stdout
    out.write(json.dumps(payload, separators=(",", ":")))
    out.write("\n")
    out.flush()


def _service_name() -> str:
    return os.environ.get("LITEHORSE_SERVICE", "api")
