"""Test-suite metric derived from CSC testcase artifacts."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

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


_EXECUTABLE_STATUSES = frozenset({"bootstrap", "sat", "batch_safe", "counterexample"})
_INTEGER_TEXT = re.compile(r"^[+-]?\d+$")


class TestSuiteMetricProvider:
    spec = MetricSpec(
        id="tests.suite",
        definition_version=1,
        data_type="core.vector",
        data_version=1,
        producer="test-suite-metrics",
        producer_version="1",
        stages=("csc", "tbfv", "localization"),
    )

    def collect(self, context: MetricContext) -> MetricObservation:
        source, unavailable = source_or_unavailable(context, "csc", "Test Suite")
        if unavailable is not None:
            return unavailable
        assert source is not None
        stats_payload, unavailable = artifact_or_unavailable(
            context, source, "cct_stats.json", "Test Suite"
        )
        if unavailable is not None:
            return unavailable
        records_payload, unavailable = artifact_or_unavailable(
            context, source, "testcases.json", "Test Suite"
        )
        if unavailable is not None:
            return unavailable

        stats = require_object(require_object(stats_payload, "cct_stats").get("testcases"), "cct_stats.testcases")
        records = require_array(records_payload, "testcases")
        generated = len(records)
        executable = sum(
            1
            for record in records
            if isinstance(record, Mapping) and record.get("status") in _EXECUTABLE_STATUSES
        )
        trace_backed = sum(
            1
            for record in records
            if isinstance(record, Mapping) and bool(record.get("trace_path"))
        )
        unique_inputs = len({
            _canonical_inputs(record["inputs"])
            for record in records
            if isinstance(record, Mapping) and isinstance(record.get("inputs"), Mapping)
        })
        invalid = generated - executable

        reported = {
            "generated_records": require_count(stats.get("generated_records"), "testcases.generated_records"),
            "executable_records": require_count(stats.get("executable_records"), "testcases.executable_records"),
            "trace_backed_records": require_count(stats.get("trace_backed_records"), "testcases.trace_backed_records"),
        }
        observed = {
            "generated_records": generated,
            "executable_records": executable,
            "trace_backed_records": trace_backed,
        }
        if reported != observed:
            raise ValueError(f"testcase artifacts disagree: reported={reported}, observed={observed}")

        return MetricObservation(
            status="available",
            data={
                "primaryKey": "executable_records",
                "measurements": [
                    measurement("generated_records", "Generated", generated, "test_record"),
                    measurement("executable_records", "Executable", executable, "test_record"),
                    measurement("unique_inputs", "Unique inputs", unique_inputs, "input"),
                    measurement("trace_backed_records", "Trace-backed", trace_backed, "test_record"),
                    measurement("invalid_records", "Invalid", invalid, "test_record"),
                ],
            },
            source_job_id=source.job_id,
            source_stage=source.stage,
            artifacts=(("cct_stats.json", "source"), ("testcases.json", "source")),
        )


def _canonical_inputs(inputs: Mapping[str, Any]) -> str:
    normalized = {
        str(name): _normalize_input_value(value)
        for name, value in sorted(inputs.items(), key=lambda item: str(item[0]))
    }
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _normalize_input_value(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        if _INTEGER_TEXT.fullmatch(stripped):
            return int(stripped)
        if stripped.lower() in {"true", "false"}:
            return stripped.lower() == "true"
    return value
