"""Runtime contract for Analysis Metrics v1 artifacts.

The JSON Schema documents the transport shape.  This module additionally
enforces cross-field rules that JSON Schema cannot express conveniently, such
as unique metric IDs, vector primary keys, and safe provenance paths.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from datetime import datetime
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Any


METRICS_FORMAT = "analysis.metrics"
METRICS_VERSION = 1
CORE_DATA_VERSION = 1
METRIC_STATUSES = frozenset({
    "available",
    "partial",
    "pending",
    "not_applicable",
    "unavailable",
    "unsupported",
    "error",
})

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_.-]*$")
_DATA_TYPE = re.compile(r"^[a-z][a-z0-9_.-]*\.[a-z][a-z0-9_.-]*$")
_EXTENSION_KEY = re.compile(r"^[a-z][a-z0-9_.-]*/[a-z][a-z0-9_.-]*$")
_DATA_REQUIRED_STATUSES = frozenset({"available", "partial"})
_REASON_REQUIRED_STATUSES = frozenset({
    "partial",
    "unavailable",
    "unsupported",
    "error",
})
_DATA_FORBIDDEN_STATUSES = METRIC_STATUSES - _DATA_REQUIRED_STATUSES


class MetricsContractError(ValueError):
    """Raised when an Analysis Metrics document violates the v1 contract."""


def validate_metrics_document(document: Any) -> dict[str, Any]:
    """Validate and return a defensive copy of an Analysis Metrics v1 document."""
    root = _require_mapping(document, "document")
    _require_exact_keys(
        root,
        required={
            "format",
            "version",
            "jobId",
            "stage",
            "generatedAt",
            "registryVersion",
            "lineage",
            "metrics",
        },
        optional=set(),
        field="document",
    )
    if root["format"] != METRICS_FORMAT:
        raise MetricsContractError("document.format must be 'analysis.metrics'")
    if root["version"] != METRICS_VERSION:
        raise MetricsContractError("document.version must be 1")
    job_id = _require_non_empty_string(root["jobId"], "document.jobId")
    _require_identifier(root["stage"], "document.stage")
    _require_timestamp(root["generatedAt"], "document.generatedAt")
    _require_plain_int(root["registryVersion"], "document.registryVersion", minimum=1)

    lineage = _require_sequence(root["lineage"], "document.lineage")
    lineage_job_ids: set[str] = set()
    for index, entry in enumerate(lineage):
        entry_field = f"document.lineage[{index}]"
        record = _require_mapping(entry, entry_field)
        _require_exact_keys(
            record,
            required={"jobId", "stage", "relation"},
            optional=set(),
            field=entry_field,
        )
        upstream_id = _require_non_empty_string(record["jobId"], f"{entry_field}.jobId")
        if upstream_id == job_id:
            raise MetricsContractError(f"{entry_field}.jobId must not reference the current job")
        if upstream_id in lineage_job_ids:
            raise MetricsContractError(f"duplicate lineage job ID: {upstream_id}")
        lineage_job_ids.add(upstream_id)
        _require_identifier(record["stage"], f"{entry_field}.stage")
        _require_identifier(record["relation"], f"{entry_field}.relation")

    metrics = _require_sequence(root["metrics"], "document.metrics")
    metric_ids: set[str] = set()
    valid_source_ids = lineage_job_ids | {job_id}
    for index, metric in enumerate(metrics):
        metric_id = _validate_metric(metric, f"document.metrics[{index}]", valid_source_ids)
        if metric_id in metric_ids:
            raise MetricsContractError(f"duplicate metric ID: {metric_id}")
        metric_ids.add(metric_id)

    _require_json_value(root, "document")
    return deepcopy(root)


def validate_metric_record(
    metric: Any,
    valid_source_ids: set[str],
) -> dict[str, Any]:
    """Validate one metric before it is added to a larger document."""
    if not valid_source_ids:
        raise MetricsContractError("valid_source_ids must not be empty")
    _validate_metric(metric, "metric", set(valid_source_ids))
    _require_json_value(metric, "metric")
    return deepcopy(metric)


def _validate_metric(metric: Any, field: str, valid_source_ids: set[str]) -> str:
    record = _require_mapping(metric, field)
    _require_exact_keys(
        record,
        required={
            "id",
            "definitionVersion",
            "status",
            "scope",
            "dataType",
            "dataVersion",
            "provenance",
            "extensions",
        },
        optional={"data", "reason", "details"},
        field=field,
    )
    metric_id = _require_identifier(record["id"], f"{field}.id")
    _require_plain_int(record["definitionVersion"], f"{field}.definitionVersion", minimum=1)
    status = record["status"]
    if status not in METRIC_STATUSES:
        raise MetricsContractError(f"{field}.status is not supported: {status!r}")
    data_type = _require_data_type(record["dataType"], f"{field}.dataType")
    data_version = _require_plain_int(record["dataVersion"], f"{field}.dataVersion", minimum=1)

    has_data = "data" in record
    has_reason = "reason" in record
    if status in _DATA_REQUIRED_STATUSES and not has_data:
        raise MetricsContractError(f"{field}.data is required for status {status!r}")
    if status in _DATA_FORBIDDEN_STATUSES and has_data:
        raise MetricsContractError(f"{field}.data is not allowed for status {status!r}")
    if status in _REASON_REQUIRED_STATUSES and not has_reason:
        raise MetricsContractError(f"{field}.reason is required for status {status!r}")
    if status == "available" and has_reason:
        raise MetricsContractError(f"{field}.reason is not allowed for status 'available'")

    _validate_scope(record["scope"], f"{field}.scope", valid_source_ids)
    if has_reason:
        _validate_reason(record["reason"], f"{field}.reason")
    if has_data:
        _validate_data(data_type, data_version, record["data"], f"{field}.data")
    if "details" in record:
        _validate_details(record["details"], f"{field}.details")
    _validate_provenance(record["provenance"], f"{field}.provenance")
    _validate_extensions(record["extensions"], f"{field}.extensions")
    return metric_id


def _validate_scope(scope: Any, field: str, valid_source_ids: set[str]) -> None:
    record = _require_mapping(scope, field)
    _require_exact_keys(record, {"sourceJobId", "stage"}, set(), field)
    source_job_id = _require_non_empty_string(record["sourceJobId"], f"{field}.sourceJobId")
    if source_job_id not in valid_source_ids:
        raise MetricsContractError(
            f"{field}.sourceJobId does not reference the current job or its lineage: "
            f"{source_job_id}"
        )
    _require_identifier(record["stage"], f"{field}.stage")


def _validate_reason(reason: Any, field: str) -> None:
    record = _require_mapping(reason, field)
    _require_exact_keys(record, {"code", "message"}, set(), field)
    _require_identifier(record["code"], f"{field}.code")
    _require_non_empty_string(record["message"], f"{field}.message")


def _validate_details(details: Any, field: str) -> None:
    record = _require_mapping(details, field)
    _require_exact_keys(record, {"kind", "artifact"}, set(), field)
    if record["kind"] != "artifact":
        raise MetricsContractError(f"{field}.kind must be 'artifact'")
    _require_safe_artifact_path(record["artifact"], f"{field}.artifact")


def _validate_provenance(provenance: Any, field: str) -> None:
    record = _require_mapping(provenance, field)
    _require_exact_keys(record, {"producer", "producerVersion", "artifacts"}, set(), field)
    _require_identifier(record["producer"], f"{field}.producer")
    _require_non_empty_string(record["producerVersion"], f"{field}.producerVersion")
    artifacts = _require_sequence(record["artifacts"], f"{field}.artifacts")
    seen: set[str] = set()
    for index, artifact in enumerate(artifacts):
        artifact_field = f"{field}.artifacts[{index}]"
        artifact_record = _require_mapping(artifact, artifact_field)
        _require_exact_keys(artifact_record, {"path", "role"}, set(), artifact_field)
        path = _require_safe_artifact_path(artifact_record["path"], f"{artifact_field}.path")
        if path in seen:
            raise MetricsContractError(f"duplicate provenance artifact path: {path}")
        seen.add(path)
        _require_identifier(artifact_record["role"], f"{artifact_field}.role")


def _validate_extensions(extensions: Any, field: str) -> None:
    record = _require_mapping(extensions, field)
    for key, value in record.items():
        if not isinstance(key, str) or not _EXTENSION_KEY.fullmatch(key):
            raise MetricsContractError(f"{field} has invalid extension key: {key!r}")
        _require_json_value(value, f"{field}.{key}")


def _validate_data(data_type: str, data_version: int, data: Any, field: str) -> None:
    record = _require_mapping(data, field)
    core_validator = _CORE_DATA_VALIDATORS.get((data_type, data_version))
    if core_validator is not None:
        core_validator(record, field)
        return
    if data_type.startswith("core."):
        if data_version != CORE_DATA_VERSION:
            raise MetricsContractError(
                f"{field} uses unsupported {data_type} data version: {data_version}"
            )
        raise MetricsContractError(f"unknown core data type: {data_type}")
    _require_json_value(record, field)


def _validate_scalar_data(data: Mapping[str, Any], field: str) -> None:
    _require_exact_keys(data, {"value", "unit"}, {"components"}, field)
    _require_number(data["value"], f"{field}.value")
    _require_identifier(data["unit"], f"{field}.unit")
    if "components" in data:
        _validate_components(data["components"], f"{field}.components")


def _validate_ratio_data(data: Mapping[str, Any], field: str) -> None:
    _require_exact_keys(data, {"numerator", "denominator", "unit"}, {"components"}, field)
    numerator = _require_number(data["numerator"], f"{field}.numerator", minimum=0)
    denominator = _require_number(
        data["denominator"], f"{field}.denominator", exclusive_minimum=0
    )
    if numerator > denominator:
        raise MetricsContractError(f"{field}.numerator must not exceed denominator")
    _require_identifier(data["unit"], f"{field}.unit")
    if "components" in data:
        _validate_components(data["components"], f"{field}.components")


def _validate_vector_data(data: Mapping[str, Any], field: str) -> None:
    _require_exact_keys(data, {"primaryKey", "measurements"}, set(), field)
    primary_key = _require_identifier(data["primaryKey"], f"{field}.primaryKey")
    keys = _validate_components(data["measurements"], f"{field}.measurements")
    if primary_key not in keys:
        raise MetricsContractError(
            f"{field}.primaryKey does not reference a measurement: {primary_key}"
        )


# Private, immutable dispatch for the three core data types.  New core types
# extend this mapping; runtime mutation is impossible by construction and no
# registration API is exposed.
_CORE_DATA_VALIDATORS: Mapping[
    tuple[str, int], Callable[[Mapping[str, Any], str], None]
] = MappingProxyType({
    ("core.scalar", CORE_DATA_VERSION): _validate_scalar_data,
    ("core.ratio", CORE_DATA_VERSION): _validate_ratio_data,
    ("core.vector", CORE_DATA_VERSION): _validate_vector_data,
})


def _validate_components(components: Any, field: str) -> set[str]:
    records = _require_sequence(components, field)
    if not records:
        raise MetricsContractError(f"{field} must not be empty")
    keys: set[str] = set()
    for index, component in enumerate(records):
        component_field = f"{field}[{index}]"
        record = _require_mapping(component, component_field)
        _require_exact_keys(record, {"key", "label", "value", "unit"}, set(), component_field)
        key = _require_identifier(record["key"], f"{component_field}.key")
        if key in keys:
            raise MetricsContractError(f"duplicate component key in {field}: {key}")
        keys.add(key)
        _require_non_empty_string(record["label"], f"{component_field}.label")
        _require_number(record["value"], f"{component_field}.value")
        _require_identifier(record["unit"], f"{component_field}.unit")
    return keys


def _require_mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MetricsContractError(f"{field} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise MetricsContractError(f"{field} keys must be strings")
    return value


def _require_sequence(value: Any, field: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise MetricsContractError(f"{field} must be an array")
    return value


def _require_exact_keys(
    value: Mapping[str, Any],
    required: set[str],
    optional: set[str],
    field: str,
) -> None:
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - required - optional)
    if missing:
        raise MetricsContractError(f"{field} is missing required fields: {missing}")
    if unknown:
        raise MetricsContractError(f"{field} has unknown fields: {unknown}")


def _require_non_empty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise MetricsContractError(f"{field} must be a non-empty string")
    return value


def _require_identifier(value: Any, field: str) -> str:
    text = _require_non_empty_string(value, field)
    if not _IDENTIFIER.fullmatch(text):
        raise MetricsContractError(f"{field} must be a lowercase identifier")
    return text


def _require_data_type(value: Any, field: str) -> str:
    text = _require_non_empty_string(value, field)
    if not _DATA_TYPE.fullmatch(text):
        raise MetricsContractError(f"{field} must be a namespaced data type")
    return text


def _require_plain_int(value: Any, field: str, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MetricsContractError(f"{field} must be an integer")
    if minimum is not None and value < minimum:
        raise MetricsContractError(f"{field} must be at least {minimum}")
    return value


def _require_number(
    value: Any,
    field: str,
    minimum: float | None = None,
    exclusive_minimum: float | None = None,
) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise MetricsContractError(f"{field} must be a finite number")
    if minimum is not None and value < minimum:
        raise MetricsContractError(f"{field} must be at least {minimum}")
    if exclusive_minimum is not None and value <= exclusive_minimum:
        raise MetricsContractError(f"{field} must be greater than {exclusive_minimum}")
    return value


def _require_timestamp(value: Any, field: str) -> str:
    text = _require_non_empty_string(value, field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MetricsContractError(f"{field} must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise MetricsContractError(f"{field} must include a timezone")
    return text


def _require_safe_artifact_path(value: Any, field: str) -> str:
    text = _require_non_empty_string(value, field)
    path = PurePosixPath(text)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise MetricsContractError(f"{field} must be a safe job-relative path")
    return text


def _require_json_value(value: Any, field: str) -> None:
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise MetricsContractError(f"{field} must contain only finite JSON values") from exc
