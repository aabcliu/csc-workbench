"""Success finalizer that projects each stage's facts into an Analysis Outcome."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from csc_engine.outcomes import (
    DEFAULT_OUTCOME_FILENAME,
    project_outcome,
    load_outcome_document,
    write_outcome_document,
)

from .analysis_sources import STAGE_BY_KIND, AnalysisSourceResolver
from .jobs import JobManager
from .models import JobRecord, JobStatus


def load_job_outcome(jobs: JobManager, job: JobRecord) -> dict[str, Any]:
    """Read only this succeeded job's successfully finalized Outcome.

    KeyError means unavailable; OutcomeContractError means invalid evidence.
    Both the read API and start gate use this boundary, including when an old
    copied artifact remains after a failed finalizer.
    """
    if job.status is not JobStatus.succeeded or any(
        warning.component == "outcome" for warning in job.finalization_warnings
    ):
        raise KeyError(DEFAULT_OUTCOME_FILENAME)
    return load_outcome_document(
        jobs.outcome_path(job.id), expected_job_id=job.id, expected_stage=job.kind
    )


class AnalysisOutcomeService:
    """Project the authoritative stage facts into ``analysis.outcome.v1.json``.

    Used as a ``SuccessFinalizer`` alongside the metrics finalizer.  Fact
    failures never raise here: the projector fails closed into an ``unknown``
    conclusion, so a succeeded job still carries a trustworthy conclusion.
    """

    artifact_name = DEFAULT_OUTCOME_FILENAME
    component = "outcome"

    def __init__(self, jobs: JobManager, resolver: AnalysisSourceResolver | None = None):
        self.jobs = jobs
        self.resolver = resolver or AnalysisSourceResolver(jobs)

    def __call__(self, job: JobRecord, *, output_path: Path | None = None) -> Path:
        stage = STAGE_BY_KIND.get(job.kind)
        if stage is None:
            raise ValueError(f"unsupported job kind for analysis outcome: {job.kind}")
        if not job.result_dir:
            raise ValueError(f"job {job.id} has no result directory")
        if not job.started_at or not job.finished_at:
            raise ValueError(f"job {job.id} has incomplete timestamps")

        result_dir = self.resolver.result_dir(job)
        upstream_job_id: str | None = None
        upstream_result_root: Path | None = None
        if job.kind == "localization":
            upstream = self.resolver.upstream_record(job, "tbfv_job_id", "tbfv")
            if upstream is not None:
                upstream_job_id = upstream.id
                if not any(w.component == "outcome" for w in upstream.finalization_warnings):
                    upstream_result_root = self.resolver.result_dir(upstream)

        document = project_outcome(
            stage,
            job_id=job.id,
            generated_at=job.finished_at,
            source_root=result_dir,
            upstream_job_id=upstream_job_id,
            upstream_result_root=upstream_result_root,
        )
        return write_outcome_document(
            output_path or result_dir / DEFAULT_OUTCOME_FILENAME,
            document,
        )


def upstream_provenance(jobs: JobManager, job: JobRecord) -> list[dict[str, Any]]:
    """View-level lineage, never a modification of the stage's own verdict."""
    resolver = AnalysisSourceResolver(jobs)
    result = []
    current = job
    for kind, key in (("tbfv", "tbfv_job_id"), ("csc", "csc_job_id")):
        if (current.kind, kind) not in {("localization", "tbfv"), ("tbfv", "csc")}:
            continue
        upstream = resolver.upstream_record(current, key, kind)
        if upstream is None:
            result.append({"stage": kind, "state": "unavailable", "reason": "Upstream lineage is missing or invalid."})
            break
        entry = {"stage": kind, "jobId": upstream.id, "state": "unavailable"}
        try:
            document = load_job_outcome(jobs, upstream)
            entry.update(state="ready", conclusion=document["conclusion"])
        except (KeyError, ValueError, OSError):
            entry["reason"] = "The upstream conclusion could not be read."
        result.append(entry)
        current = upstream
    return result
