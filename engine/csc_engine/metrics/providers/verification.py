"""Verification-result metric derived from Refined TBFV reports."""

from __future__ import annotations

from ..context import MetricContext
from ..registry import MetricObservation, MetricSpec
from .common import (
    artifact_or_unavailable,
    measurement,
    require_array,
    require_count,
    require_object,
    source_or_unavailable,
)


_STATUS_KEYS = {
    "pass": "passed",
    "fail": "failed",
    "unknown": "unknown",
    "skipped": "skipped",
    "unsupported": "unsupported",
}


class VerificationResultsMetricProvider:
    spec = MetricSpec(
        id="verification.results",
        definition_version=2,
        data_type="core.vector",
        data_version=1,
        producer="tbfv-metrics",
        producer_version="1",
        stages=("tbfv", "localization"),
    )

    def collect(self, context: MetricContext) -> MetricObservation:
        source, unavailable = source_or_unavailable(context, "tbfv", "Verification Results")
        if unavailable is not None:
            return unavailable
        assert source is not None
        payload, unavailable = artifact_or_unavailable(
            context, source, "refined_tbfv_report.json", "Verification Results"
        )
        if unavailable is not None:
            return unavailable

        report = require_object(payload, "verification report")
        summary = require_object(report.get("summary"), "verification report.summary")
        results = require_array(report.get("results"), "verification report.results")
        observed = {key: 0 for key in _STATUS_KEYS.values()}
        for index, result in enumerate(results):
            record = require_object(result, f"verification report.results[{index}]")
            status = record.get("status")
            if status not in _STATUS_KEYS:
                raise ValueError(f"unsupported verification status at result {index}: {status!r}")
            observed[_STATUS_KEYS[status]] += 1

        reported = {
            key: require_count(summary.get(key), f"verification report.summary.{key}")
            for key in observed
        }
        if reported != observed:
            raise ValueError(f"verification report summary disagrees with results: reported={reported}, observed={observed}")
        testcases = require_count(summary.get("testcases"), "verification report.summary.testcases")
        fsf_units = require_count(summary.get("fsf_units"), "verification report.summary.fsf_units")
        checked = observed["passed"] + observed["failed"]

        return MetricObservation(
            status="available",
            data={
                "primaryKey": "passed",
                "measurements": [
                    measurement("checked", "Checked", checked, "verification_check"),
                    measurement("passed", "Passed", observed["passed"], "verification_check"),
                    measurement("failed", "Failed", observed["failed"], "verification_check"),
                    measurement("skipped", "Skipped", observed["skipped"], "verification_check"),
                    measurement("unsupported", "Unsupported", observed["unsupported"], "verification_check"),
                    measurement("unknown", "Unknown", observed["unknown"], "verification_check"),
                    measurement("testcases", "Test cases", testcases, "test_record"),
                    measurement("fsf_units", "FSF units", fsf_units, "fsf_unit"),
                ],
            },
            source_job_id=source.job_id,
            source_stage=source.stage,
            artifacts=(("refined_tbfv_report.json", "source"),),
        )
