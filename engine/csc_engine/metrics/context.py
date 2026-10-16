"""Read-only, cached access to metric source artifacts."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


class MetricContextError(ValueError):
    """Raised for an invalid source or artifact reference."""


_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_.-]*$")


@dataclass(frozen=True)
class MetricSource:
    job_id: str
    stage: str
    result_dir: Path
    relation: str | None = None
    started_at: str | None = None
    finished_at: str | None = None

    def __post_init__(self) -> None:
        if not self.job_id:
            raise MetricContextError("metric source job_id must not be empty")
        if not _IDENTIFIER.fullmatch(self.stage):
            raise MetricContextError(f"invalid metric source stage: {self.stage!r}")
        if self.relation is not None and not _IDENTIFIER.fullmatch(self.relation):
            raise MetricContextError(f"invalid metric source relation: {self.relation!r}")
        started = _parse_timestamp(self.started_at, "started_at")
        finished = _parse_timestamp(self.finished_at, "finished_at")
        if finished is not None and started is None:
            raise MetricContextError("metric source finished_at requires started_at")
        if started is not None and finished is not None and finished < started:
            raise MetricContextError("metric source finished_at must not precede started_at")
        object.__setattr__(self, "result_dir", Path(self.result_dir).resolve())

    def wall_duration_ms(self) -> int | None:
        started = _parse_timestamp(self.started_at, "started_at")
        finished = _parse_timestamp(self.finished_at, "finished_at")
        if started is None or finished is None:
            return None
        return max(0, round((finished - started).total_seconds() * 1000))


class MetricContext:
    """Context shared by providers during one metric collection pass.

    JSON artifacts are parsed at most once per source and path.  Providers get
    defensive copies so one provider cannot mutate facts seen by another.
    """

    def __init__(
        self,
        current: MetricSource,
        upstream: Iterable[MetricSource] = (),
    ) -> None:
        if current.relation is not None:
            raise MetricContextError("the current metric source must not have a relation")
        self.current = current
        self._sources: dict[str, MetricSource] = {current.job_id: current}
        for source in upstream:
            if source.relation is None:
                raise MetricContextError(
                    f"upstream metric source {source.job_id!r} must declare a relation"
                )
            if source.job_id in self._sources:
                raise MetricContextError(f"duplicate metric source job ID: {source.job_id}")
            self._sources[source.job_id] = source
        self._json_cache: dict[tuple[str, str], Any] = {}

    @property
    def job_id(self) -> str:
        return self.current.job_id

    @property
    def stage(self) -> str:
        return self.current.stage

    def source(self, job_id: str | None = None) -> MetricSource:
        resolved_id = job_id or self.current.job_id
        try:
            return self._sources[resolved_id]
        except KeyError as exc:
            raise MetricContextError(f"unknown metric source job ID: {resolved_id}") from exc

    def sources(self) -> tuple[MetricSource, ...]:
        return tuple(self._sources.values())

    def source_for_stage(self, stage: str) -> MetricSource:
        matches = [source for source in self._sources.values() if source.stage == stage]
        if not matches:
            raise MetricContextError(f"no metric source is available for stage: {stage}")
        if len(matches) > 1:
            raise MetricContextError(f"multiple metric sources are available for stage: {stage}")
        return matches[0]

    def lineage_records(self) -> list[dict[str, str]]:
        return [
            {
                "jobId": source.job_id,
                "stage": source.stage,
                "relation": source.relation or "",
            }
            for source in self._sources.values()
            if source.job_id != self.current.job_id
        ]

    def artifact_path(self, relative_path: str, source_job_id: str | None = None) -> Path:
        if not isinstance(relative_path, str) or not relative_path:
            raise MetricContextError("artifact path must be a non-empty string")
        source = self.source(source_job_id)
        candidate = (source.result_dir / relative_path).resolve()
        if source.result_dir not in candidate.parents:
            raise MetricContextError(f"artifact path escapes its result directory: {relative_path}")
        return candidate

    def has_artifact(self, relative_path: str, source_job_id: str | None = None) -> bool:
        return self.artifact_path(relative_path, source_job_id).is_file()

    def load_json(self, relative_path: str, source_job_id: str | None = None) -> Any:
        source = self.source(source_job_id)
        cache_key = (source.job_id, relative_path)
        if cache_key not in self._json_cache:
            path = self.artifact_path(relative_path, source.job_id)
            if not path.is_file():
                raise MetricContextError(f"metric artifact does not exist: {relative_path}")
            try:
                self._json_cache[cache_key] = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise MetricContextError(
                    f"unable to read metric artifact {relative_path}: {exc}"
                ) from exc
        return deepcopy(self._json_cache[cache_key])


def _parse_timestamp(value: str | None, field: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise MetricContextError(f"metric source {field} must be a non-empty string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MetricContextError(f"metric source {field} must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise MetricContextError(f"metric source {field} must include a timezone")
    return parsed
