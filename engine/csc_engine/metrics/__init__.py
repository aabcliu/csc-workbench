"""Extensible analysis-metrics infrastructure."""

from .collect import collect_metrics
from .context import MetricContext, MetricContextError, MetricSource
from .contract import (
    CORE_DATA_VERSION,
    METRICS_FORMAT,
    METRICS_VERSION,
    METRIC_STATUSES,
    MetricsContractError,
    validate_metric_record,
    validate_metrics_document,
)
from .registry import (
    MetricObservation,
    MetricProvider,
    MetricRegistry,
    MetricRegistryError,
    MetricSpec,
)
from .store import (
    DEFAULT_METRICS_FILENAME,
    load_metrics_document,
    write_metrics_document,
)
from .providers import DEFAULT_METRIC_PROVIDERS, create_default_metric_registry

__all__ = [
    "CORE_DATA_VERSION",
    "DEFAULT_METRIC_PROVIDERS",
    "DEFAULT_METRICS_FILENAME",
    "METRICS_FORMAT",
    "METRICS_VERSION",
    "METRIC_STATUSES",
    "MetricContext",
    "MetricContextError",
    "MetricObservation",
    "MetricProvider",
    "MetricRegistry",
    "MetricRegistryError",
    "MetricSource",
    "MetricSpec",
    "MetricsContractError",
    "collect_metrics",
    "create_default_metric_registry",
    "load_metrics_document",
    "validate_metric_record",
    "validate_metrics_document",
    "write_metrics_document",
]
