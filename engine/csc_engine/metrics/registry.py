"""Explicit provider registry for analysis metrics."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .context import MetricContext


_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_.-]*$")
_DATA_TYPE = re.compile(r"^[a-z][a-z0-9_.-]*\.[a-z][a-z0-9_.-]*$")


class MetricRegistryError(ValueError):
    """Raised for invalid metric definitions or registry operations."""


@dataclass(frozen=True)
class MetricSpec:
    id: str
    definition_version: int
    data_type: str
    data_version: int
    producer: str
    producer_version: str
    stages: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not _IDENTIFIER.fullmatch(self.id):
            raise MetricRegistryError(f"invalid metric ID: {self.id!r}")
        if (
            isinstance(self.definition_version, bool)
            or not isinstance(self.definition_version, int)
            or self.definition_version < 1
        ):
            raise MetricRegistryError("definition_version must be a positive integer")
        if not _DATA_TYPE.fullmatch(self.data_type):
            raise MetricRegistryError(f"invalid metric data type: {self.data_type!r}")
        if (
            isinstance(self.data_version, bool)
            or not isinstance(self.data_version, int)
            or self.data_version < 1
        ):
            raise MetricRegistryError("data_version must be a positive integer")
        if not _IDENTIFIER.fullmatch(self.producer):
            raise MetricRegistryError(f"invalid metric producer: {self.producer!r}")
        if not self.producer_version:
            raise MetricRegistryError("producer_version must not be empty")
        if len(set(self.stages)) != len(self.stages):
            raise MetricRegistryError(f"metric {self.id!r} contains duplicate stages")
        for stage in self.stages:
            if not _IDENTIFIER.fullmatch(stage):
                raise MetricRegistryError(f"invalid metric stage: {stage!r}")

    def applies_to(self, stage: str) -> bool:
        return not self.stages or stage in self.stages


@dataclass(frozen=True)
class MetricObservation:
    status: str
    data: dict[str, Any] | None = None
    reason_code: str | None = None
    reason_message: str | None = None
    source_job_id: str | None = None
    source_stage: str | None = None
    artifacts: tuple[tuple[str, str], ...] = ()
    details_artifact: str | None = None
    extensions: dict[str, Any] | None = None


@runtime_checkable
class MetricProvider(Protocol):
    spec: MetricSpec

    def collect(self, context: MetricContext) -> MetricObservation:
        """Calculate one metric from a read-only context."""


class MetricRegistry:
    """Ordered, explicit collection of metric providers."""

    def __init__(self, providers: tuple[MetricProvider, ...] = (), version: int = 1) -> None:
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise MetricRegistryError("registry version must be a positive integer")
        self.version = version
        self._providers: dict[str, MetricProvider] = {}
        for provider in providers:
            self.register(provider)

    def register(self, provider: MetricProvider) -> None:
        if not isinstance(provider, MetricProvider):
            raise MetricRegistryError("provider must expose a MetricSpec and collect()")
        if not isinstance(provider.spec, MetricSpec):
            raise MetricRegistryError("provider.spec must be a MetricSpec")
        metric_id = provider.spec.id
        if metric_id in self._providers:
            raise MetricRegistryError(f"duplicate metric provider: {metric_id}")
        self._providers[metric_id] = provider

    def unregister(self, metric_id: str) -> MetricProvider:
        try:
            return self._providers.pop(metric_id)
        except KeyError as exc:
            raise MetricRegistryError(f"metric provider is not registered: {metric_id}") from exc

    def get(self, metric_id: str) -> MetricProvider:
        try:
            return self._providers[metric_id]
        except KeyError as exc:
            raise MetricRegistryError(f"metric provider is not registered: {metric_id}") from exc

    def providers_for(self, stage: str) -> tuple[MetricProvider, ...]:
        return tuple(
            provider
            for provider in self._providers.values()
            if provider.spec.applies_to(stage)
        )

    def ids(self) -> tuple[str, ...]:
        return tuple(self._providers)
