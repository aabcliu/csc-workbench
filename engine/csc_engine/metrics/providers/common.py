"""Shared helpers for built-in metric providers."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from ..context import MetricContext, MetricContextError, MetricSource
from ..registry import MetricObservation


SOLVER_TELEMETRY_ARTIFACT = "solver.telemetry.v1.json"
_SOLVER_TELEMETRY_STAGES = ("csc", "tbfv")


def preferred_solver_telemetry_source(context: MetricContext) -> MetricSource | None:
    """Resolve the job that actually produced the solver telemetry.

    Only generation and verification run the solver; localization receives a
    copy of its upstream result directory, so telemetry found in a copying
    stage must never be attributed to it.
    """
    candidates = [
        source
        for source in context.sources()
        if source.stage in _SOLVER_TELEMETRY_STAGES
        and context.has_artifact(SOLVER_TELEMETRY_ARTIFACT, source.job_id)
    ]
    if not candidates:
        return None
    top_rank = max(_SOLVER_TELEMETRY_STAGES.index(source.stage) for source in candidates)
    preferred = [
        source
        for source in candidates
        if _SOLVER_TELEMETRY_STAGES.index(source.stage) == top_rank
    ]
    if len(preferred) != 1:
        raise ValueError("multiple equally preferred solver telemetry sources")
    return preferred[0]


def source_or_unavailable(
    context: MetricContext,
    stage: str,
    metric_label: str,
) -> tuple[MetricSource | None, MetricObservation | None]:
    try:
        return context.source_for_stage(stage), None
    except MetricContextError:
        return None, MetricObservation(
            status="unavailable",
            reason_code="source_job_missing",
            reason_message=f"{metric_label} requires a {stage} source job",
        )


def artifact_or_unavailable(
    context: MetricContext,
    source: MetricSource,
    relative_path: str,
    metric_label: str,
) -> tuple[Any | None, MetricObservation | None]:
    if not context.has_artifact(relative_path, source.job_id):
        return None, MetricObservation(
            status="unavailable",
            reason_code="artifact_missing",
            reason_message=f"{metric_label} requires {relative_path}",
            source_job_id=source.job_id,
            source_stage=source.stage,
        )
    return context.load_json(relative_path, source.job_id), None


def require_object(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    return value


def require_array(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    return value


def require_count(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def measurement(key: str, label: str, value: int | float, unit: str) -> dict[str, Any]:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"measurement {key} must be a finite number")
    return {"key": key, "label": label, "value": value, "unit": unit}
