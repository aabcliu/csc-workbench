"""Analysis wall-time metric derived from explicit job lineage."""

from __future__ import annotations

from ..context import MetricContext
from ..registry import MetricObservation, MetricSpec
from .common import (
    SOLVER_TELEMETRY_ARTIFACT,
    measurement,
    preferred_solver_telemetry_source,
    require_object,
)


_STAGE_LABELS = {
    "csc": ("generation_wall", "Generation"),
    "tbfv": ("verification_wall", "Verification"),
    "localization": ("localization_wall", "Localization"),
}
_STAGE_ORDER = ("csc", "tbfv", "localization")


class AnalysisTimeMetricProvider:
    spec = MetricSpec(
        id="runtime.analysis",
        definition_version=1,
        data_type="core.vector",
        data_version=1,
        producer="analysis-timing",
        producer_version="1",
    )

    def collect(self, context: MetricContext) -> MetricObservation:
        durations: dict[str, int] = {}
        missing: list[str] = []
        sources = sorted(
            context.sources(),
            key=lambda source: (
                _STAGE_ORDER.index(source.stage)
                if source.stage in _STAGE_ORDER
                else len(_STAGE_ORDER),
                source.stage,
            ),
        )
        for source in sources:
            key, _label = _STAGE_LABELS.get(
                source.stage,
                (f"{source.stage}_wall", source.stage.replace("_", " ").title()),
            )
            if key in durations:
                raise ValueError(f"multiple timing sources map to {key}")
            duration = source.wall_duration_ms()
            if duration is None:
                missing.append(source.stage)
            else:
                durations[key] = duration

        if not durations:
            return MetricObservation(
                status="unavailable",
                reason_code="timestamps_missing",
                reason_message="No completed job timestamps are available",
            )

        measurements = [
            measurement("wall_total", "Total wall time", sum(durations.values()), "millisecond")
        ]
        for source in sources:
            key, label = _STAGE_LABELS.get(
                source.stage,
                (f"{source.stage}_wall", source.stage.replace("_", " ").title()),
            )
            if key in durations:
                measurements.append(measurement(key, label, durations[key], "millisecond"))

        latest = preferred_solver_telemetry_source(context)
        if latest is not None:
            telemetry = require_object(
                context.load_json(SOLVER_TELEMETRY_ARTIFACT, latest.job_id),
                "solver telemetry",
            )
            cumulative = telemetry.get("cumulativeMs")
            if isinstance(cumulative, bool) or not isinstance(cumulative, (int, float)) or cumulative < 0:
                raise ValueError("solver telemetry cumulativeMs must be non-negative")
            measurements.append(measurement(
                "solver_cumulative", "Solver cumulative", cumulative, "millisecond"
            ))

        observation = {
            "status": "partial" if missing else "available",
            "data": {"primaryKey": "wall_total", "measurements": measurements},
        }
        if missing:
            observation.update({
                "reason_code": "timestamps_missing",
                "reason_message": f"Missing completed timestamps for stages: {', '.join(missing)}",
            })
        return MetricObservation(**observation)
