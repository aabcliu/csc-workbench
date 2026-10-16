"""Solver outcome provider backed by complete process-level telemetry."""

from __future__ import annotations

from ..context import MetricContext
from ..registry import MetricObservation, MetricSpec
from .common import (
    SOLVER_TELEMETRY_ARTIFACT,
    artifact_or_unavailable,
    measurement,
    preferred_solver_telemetry_source,
    require_count,
    require_object,
    source_or_unavailable,
)


class SolverOutcomesMetricProvider:
    spec = MetricSpec(
        id="solver.outcomes", definition_version=1, data_type="core.vector", data_version=1,
        producer="z3-telemetry", producer_version="1", stages=("csc", "tbfv", "localization"),
    )

    def collect(self, context: MetricContext) -> MetricObservation:
        source = preferred_solver_telemetry_source(context)
        if source is None:
            fallback, unavailable = source_or_unavailable(context, "csc", "Solver Outcomes")
            if unavailable is not None:
                return unavailable
            assert fallback is not None
            source = fallback
        payload, unavailable = artifact_or_unavailable(
            context, source, SOLVER_TELEMETRY_ARTIFACT, "Solver Outcomes"
        )
        if unavailable is not None:
            return unavailable
        fact = require_object(payload, "solver telemetry")
        if fact.get("format") != "solver.telemetry" or fact.get("version") != 1:
            raise ValueError("unsupported solver telemetry format/version")
        stage_fact = fact
        segments = fact.get("segments")
        if segments is not None:
            segment_map = require_object(segments, "solver telemetry segments")
            stage_fact = require_object(
                segment_map.get(source.stage),
                f"solver telemetry segment {source.stage}",
            )
        values = {
            key: require_count(stage_fact.get(key), key)
            for key in ("calls", "sat", "unsat", "unknown", "timeouts")
        }
        if values["calls"] != values["sat"] + values["unsat"] + values["unknown"]:
            raise ValueError("solver outcomes do not sum to calls")
        if values["timeouts"] > values["unknown"]:
            raise ValueError("solver timeouts must be a subset of unknown")
        complete = fact.get("complete") is True
        return MetricObservation(
            status="available" if complete else "partial",
            data={
                "primaryKey": "calls",
                "measurements": [measurement(key, key.replace("_", " ").title(), value, "solver_call") for key, value in values.items()],
            },
            reason_code=None if complete else "telemetry_incomplete",
            reason_message=None if complete else "Solver telemetry does not cover the complete run",
            source_job_id=source.job_id,
            source_stage=source.stage,
            artifacts=((SOLVER_TELEMETRY_ARTIFACT, "source"),),
            details_artifact=SOLVER_TELEMETRY_ARTIFACT,
        )
