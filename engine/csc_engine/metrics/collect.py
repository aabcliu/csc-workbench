"""Orchestrate provider collection into one validated metrics document."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .context import MetricContext, MetricContextError
from .contract import (
    METRICS_FORMAT,
    METRICS_VERSION,
    validate_metric_record,
    validate_metrics_document,
)
from .registry import MetricObservation, MetricProvider, MetricRegistry


def collect_metrics(
    context: MetricContext,
    registry: MetricRegistry,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Collect all applicable providers, isolating failures per metric."""
    metrics = [
        _collect_provider(provider, context)
        for provider in registry.providers_for(context.stage)
    ]
    document = {
        "format": METRICS_FORMAT,
        "version": METRICS_VERSION,
        "jobId": context.job_id,
        "stage": context.stage,
        "generatedAt": generated_at or datetime.now(timezone.utc).isoformat(),
        "registryVersion": registry.version,
        "lineage": context.lineage_records(),
        "metrics": metrics,
    }
    return validate_metrics_document(document)


def _collect_provider(
    provider: MetricProvider,
    context: MetricContext,
) -> dict[str, Any]:
    try:
        observation = provider.collect(context)
        if not isinstance(observation, MetricObservation):
            raise TypeError("provider collect() must return MetricObservation")
        record = _build_metric_record(provider, observation, context)
        return validate_metric_record(
            record,
            {source.job_id for source in context.sources()},
        )
    except Exception as exc:  # A broken optional metric must not hide the others.
        return _build_error_record(provider, context, exc)


def _build_metric_record(
    provider: MetricProvider,
    observation: MetricObservation,
    context: MetricContext,
) -> dict[str, Any]:
    spec = provider.spec
    source = context.source(observation.source_job_id)
    source_stage = observation.source_stage or source.stage
    if source_stage != source.stage:
        raise MetricContextError(
            f"metric {spec.id!r} declared stage {source_stage!r} for source "
            f"{source.job_id!r}, expected {source.stage!r}"
        )
    record: dict[str, Any] = {
        "id": spec.id,
        "definitionVersion": spec.definition_version,
        "status": observation.status,
        "scope": {
            "sourceJobId": source.job_id,
            "stage": source_stage,
        },
        "dataType": spec.data_type,
        "dataVersion": spec.data_version,
        "provenance": {
            "producer": spec.producer,
            "producerVersion": spec.producer_version,
            "artifacts": [
                {"path": path, "role": role}
                for path, role in observation.artifacts
            ],
        },
        "extensions": observation.extensions or {},
    }
    if observation.data is not None:
        record["data"] = observation.data
    if observation.reason_code is not None or observation.reason_message is not None:
        record["reason"] = {
            "code": observation.reason_code or "unspecified",
            "message": observation.reason_message or "No reason message was provided",
        }
    if observation.details_artifact is not None:
        record["details"] = {
            "kind": "artifact",
            "artifact": observation.details_artifact,
        }
    return record


def _build_error_record(
    provider: MetricProvider,
    context: MetricContext,
    error: Exception,
) -> dict[str, Any]:
    spec = provider.spec
    return {
        "id": spec.id,
        "definitionVersion": spec.definition_version,
        "status": "error",
        "scope": {
            "sourceJobId": context.job_id,
            "stage": context.stage,
        },
        "dataType": spec.data_type,
        "dataVersion": spec.data_version,
        "reason": {
            "code": "metric_collection_error",
            "message": f"{type(error).__name__}: {error}",
        },
        "provenance": {
            "producer": spec.producer,
            "producerVersion": spec.producer_version,
            "artifacts": [],
        },
        "extensions": {},
    }
