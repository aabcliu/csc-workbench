"""CSC obligation coverage provider."""

from __future__ import annotations

from ..context import MetricContext
from ..registry import MetricObservation, MetricSpec
from .common import artifact_or_unavailable, measurement, require_count, require_object, source_or_unavailable


class CSCObligationMetricProvider:
    spec = MetricSpec(
        id="csc.obligations", definition_version=1, data_type="core.ratio", data_version=1,
        producer="csc-obligation-metrics", producer_version="1",
        stages=("csc", "tbfv", "localization"),
    )

    def collect(self, context: MetricContext) -> MetricObservation:
        source, unavailable = source_or_unavailable(context, "csc", "CSC Obligations")
        if unavailable is not None:
            return unavailable
        assert source is not None
        payload, unavailable = artifact_or_unavailable(
            context, source, "csc.obligations.v1.json", "CSC Obligations"
        )
        if unavailable is not None:
            return unavailable
        fact = require_object(payload, "obligation facts")
        if fact.get("format") != "csc.obligations" or fact.get("version") != 1:
            raise ValueError("unsupported CSC obligation facts format/version")
        counts = require_object(fact.get("counts"), "obligation counts")
        expected = {"covered", "uncovered", "infeasible", "rangeExcluded", "truncated"}
        if set(counts) != expected:
            raise ValueError("CSC obligation counts have unexpected fields")
        values = {key: require_count(counts[key], f"counts.{key}") for key in expected}
        denominator = values["covered"] + values["uncovered"]
        common = dict(
            source_job_id=source.job_id,
            source_stage=source.stage,
            artifacts=(("csc.obligations.v1.json", "source"),),
        )
        if denominator == 0:
            return MetricObservation(status="not_applicable", **common)
        complete = fact.get("complete") is True
        stop_reason = fact.get("stopReason")
        if not isinstance(stop_reason, str) or not stop_reason:
            raise ValueError("obligation stopReason must be a non-empty string")
        return MetricObservation(
            status="available" if complete else "partial",
            data={
                "numerator": values["covered"],
                "denominator": denominator,
                "unit": "csc_obligation",
                "components": [
                    measurement("uncovered", "Uncovered", values["uncovered"], "csc_obligation"),
                    measurement("infeasible", "Infeasible", values["infeasible"], "csc_obligation"),
                    measurement("range_excluded", "Range-excluded", values["rangeExcluded"], "csc_obligation"),
                    measurement("truncated", "Truncated", values["truncated"], "csc_obligation"),
                ],
            },
            reason_code=None if complete else "exploration_incomplete",
            reason_message=None if complete else f"Exploration stopped before a fixed point: {stop_reason}",
            details_artifact="csc.obligations.v1.json",
            extensions={"csc/stop_reason": stop_reason},
            **common,
        )

