"""Shared resolution of analysis job sources and their upstream lineage.

Both the Analysis Metrics and Analysis Outcome finalizers read facts from the
same result directories, so the safety rules that decide which directories and
records are trustworthy live here: a result directory must stay inside its job
directory, and an upstream reference from user config must resolve to a
succeeded peer of the same workspace with the expected stage.
"""

from __future__ import annotations

from pathlib import Path

from .jobs import JobManager
from .models import JobRecord, JobStatus


STAGE_BY_KIND = {
    "csc": "csc",
    "tbfv": "tbfv",
    "localization": "localization",
}


class AnalysisSourceResolver:
    def __init__(self, jobs: JobManager):
        self.jobs = jobs

    def result_dir(self, job: JobRecord) -> Path:
        if not job.result_dir:
            raise ValueError(f"job {job.id} has no result directory")
        job_root = self.jobs.job_dir(job.id).resolve()
        result_dir = Path(job.result_dir).resolve()
        if job_root not in result_dir.parents or not result_dir.is_dir():
            raise ValueError(f"job {job.id} result directory is outside its job directory")
        return result_dir

    def upstream_record(
        self,
        job: JobRecord,
        config_key: str,
        expected_kind: str,
    ) -> JobRecord | None:
        """Accept an upstream only when it is a succeeded peer of this workspace.

        Config values are user input, so a lineage entry has to be verified
        against the referenced record: a wrong stage, a job from another
        workspace, a job that never finished, or a reference loop would
        otherwise attribute foreign facts to this result.
        """
        job_id = job.config.get(config_key)
        if not isinstance(job_id, str) or not job_id:
            return None
        try:
            record = self.jobs.get(job_id)
        except KeyError:
            return None
        if (
            record.kind != expected_kind
            or record.workspace_id != job.workspace_id
            or record.status is not JobStatus.succeeded
            or not record.result_dir
            or not record.started_at
            or not record.finished_at
        ):
            return None
        try:
            self.result_dir(record)
        except ValueError:
            return None
        return record
