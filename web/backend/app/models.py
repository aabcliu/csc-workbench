from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStatus(str, Enum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class ArchiveOrigin(BaseModel):
    session_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    root_job_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    producer_version: str
    imported_at: str = Field(default_factory=utc_now)


class WorkspaceRecord(BaseModel):
    id: str
    name: str
    class_name: str
    java_filename: str
    fsf_filename: str | None = None
    created_at: str = Field(default_factory=utc_now)
    archive: ArchiveOrigin | None = None


class FinalizationWarning(BaseModel):
    component: str
    code: str
    message: str


class JobRecord(BaseModel):
    id: str
    kind: str
    workspace_id: str
    status: JobStatus = JobStatus.pending
    created_at: str = Field(default_factory=utc_now)
    started_at: str | None = None
    finished_at: str | None = None
    return_code: int | None = None
    error: str | None = None
    command: list[str] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)
    result_dir: str | None = None
    finalization_warnings: list[FinalizationWarning] = Field(default_factory=list)
    archive: ArchiveOrigin | None = None


class ArtifactRecord(BaseModel):
    path: str
    name: str
    size: int
    media_type: str


class InputInterval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min: int = Field(strict=True)
    max: int = Field(strict=True)

    @model_validator(mode="after")
    def validate_order(self):
        if self.min > self.max:
            raise ValueError("input interval min must not exceed max")
        return self


class CSCJobRequest(BaseModel):
    workspace_id: str
    mode: str = Field(default="expanded", pattern="^(original|expanded)$")
    range_bound: int | None = Field(default=None, ge=1, le=1_000_000)
    input_intervals: dict[str, InputInterval] | None = None
    max_iter: int = Field(default=30, ge=1, le=10_000)
    strategy: str = Field(default="batch", pattern="^(sequential|batch)$")
    workers: int = Field(default=4, ge=1, le=128)
    bootstrap: str | None = None
    render_cct: bool = True

    @model_validator(mode="after")
    def normalize_interval_source(self):
        if self.range_bound is not None and self.input_intervals is not None:
            raise ValueError("provide either range_bound or input_intervals, not both")
        if self.mode == "original" and self.input_intervals is not None:
            raise ValueError("input_intervals require expanded mode")
        if self.range_bound is None and self.input_intervals is None and self.mode == "expanded":
            self.range_bound = 200
        return self


class TBFVJobRequest(BaseModel):
    workspace_id: str
    csc_job_id: str
    render_cct: bool = True


class LocalizationJobRequest(BaseModel):
    workspace_id: str
    tbfv_job_id: str
    scoring_strategy: Literal["ochiai", "dstar", "op2"] = "ochiai"


def media_type_for(path: Path) -> str:
    return {
        ".json": "application/json",
        ".jsonl": "application/x-ndjson",
        ".svg": "image/svg+xml",
        ".pdf": "application/pdf",
        ".txt": "text/plain",
        ".log": "text/plain",
        ".dot": "text/vnd.graphviz",
    }.get(path.suffix.lower(), "application/octet-stream")
