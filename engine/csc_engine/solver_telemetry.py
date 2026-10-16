"""Process-local, thread-safe telemetry for all CSC Z3 checks."""

from __future__ import annotations

import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class SolverTelemetrySnapshot:
    calls: int
    sat: int
    unsat: int
    unknown: int
    timeouts: int
    cumulative_ms: float

    def to_artifact(self, stage: str | None = None) -> dict:
        artifact = {
            "format": "solver.telemetry",
            "version": 1,
            "complete": True,
            "calls": self.calls,
            "sat": self.sat,
            "unsat": self.unsat,
            "unknown": self.unknown,
            "timeouts": self.timeouts,
            "cumulativeMs": round(self.cumulative_ms, 3),
        }
        if stage is not None:
            artifact["segments"] = {
                stage: {
                    "calls": self.calls,
                    "sat": self.sat,
                    "unsat": self.unsat,
                    "unknown": self.unknown,
                    "timeouts": self.timeouts,
                    "cumulativeMs": round(self.cumulative_ms, 3),
                }
            }
        return artifact


class SolverTelemetry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._calls = 0
            self._sat = 0
            self._unsat = 0
            self._unknown = 0
            self._timeouts = 0
            self._cumulative_ms = 0.0

    def record(self, outcome: str, elapsed_ms: float, *, timeout: bool = False) -> None:
        if outcome not in {"sat", "unsat", "unknown"}:
            raise ValueError(f"unsupported solver outcome: {outcome}")
        if elapsed_ms < 0:
            raise ValueError("solver elapsed time must be non-negative")
        with self._lock:
            self._calls += 1
            setattr(self, f"_{outcome}", getattr(self, f"_{outcome}") + 1)
            if timeout:
                if outcome != "unknown":
                    raise ValueError("timeout must be an unknown outcome")
                self._timeouts += 1
            self._cumulative_ms += elapsed_ms

    def snapshot(self) -> SolverTelemetrySnapshot:
        with self._lock:
            return SolverTelemetrySnapshot(
                calls=self._calls,
                sat=self._sat,
                unsat=self._unsat,
                unknown=self._unknown,
                timeouts=self._timeouts,
                cumulative_ms=self._cumulative_ms,
            )


solver_telemetry = SolverTelemetry()


def combine_telemetry_artifacts(*artifacts: dict) -> dict:
    """Combine artifacts, replacing same-stage segments to make retries idempotent."""
    segments: dict[str, dict] = {}
    legacy: list[dict] = []
    complete = True
    for artifact in artifacts:
        _validated_totals(artifact)
        complete = complete and artifact.get("complete") is True
        artifact_segments = artifact.get("segments")
        if artifact_segments is None:
            legacy.append(artifact)
            continue
        if not isinstance(artifact_segments, dict) or not artifact_segments:
            raise ValueError("solver telemetry segments must be a non-empty object")
        for stage, segment in artifact_segments.items():
            if not isinstance(stage, str) or not stage or not isinstance(segment, dict):
                raise ValueError("solver telemetry segment is invalid")
            _validated_totals({
                "format": "solver.telemetry", "version": 1, "complete": True, **segment,
            })
            segments[stage] = dict(segment)

    total = {key: 0 for key in ("calls", "sat", "unsat", "unknown", "timeouts")}
    cumulative_ms = 0.0
    sources = legacy + [
        {"format": "solver.telemetry", "version": 1, "complete": True, **segment}
        for segment in segments.values()
    ]
    for artifact in sources:
        values, elapsed = _validated_totals(artifact)
        for key, value in values.items():
            total[key] += value
        cumulative_ms += elapsed
    result = {
        "format": "solver.telemetry",
        "version": 1,
        "complete": complete,
        **total,
        "cumulativeMs": round(cumulative_ms, 3),
    }
    if segments:
        result["segments"] = segments
    return result


def _validated_totals(artifact: dict) -> tuple[dict[str, int], float]:
    if artifact.get("format") != "solver.telemetry" or artifact.get("version") != 1:
        raise ValueError("unsupported solver telemetry format/version")
    values = {key: artifact.get(key) for key in ("calls", "sat", "unsat", "unknown", "timeouts")}
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values.values()):
        raise ValueError("solver telemetry counts must be non-negative integers")
    if values["calls"] != values["sat"] + values["unsat"] + values["unknown"]:
        raise ValueError("solver telemetry outcomes do not sum to calls")
    if values["timeouts"] > values["unknown"]:
        raise ValueError("solver timeouts must be a subset of unknown")
    elapsed = artifact.get("cumulativeMs")
    if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)) or elapsed < 0:
        raise ValueError("solver cumulativeMs must be non-negative")
    return values, float(elapsed)
