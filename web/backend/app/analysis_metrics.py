"""Success finalizer that persists one Analysis Metrics document per job."""

from __future__ import annotations

from pathlib import Path

from csc_engine.metrics import (
    DEFAULT_METRICS_FILENAME,
    MetricContext,
    MetricSource,
    collect_metrics,
    create_default_metric_registry,
    write_metrics_document,
)

from .analysis_sources import STAGE_BY_KIND, AnalysisSourceResolver
from .jobs import JobManager
from .models import JobRecord


class AnalysisMetricsService:
    """Resolve job lineage, collect metrics, and write them next to the results.

    Used as a ``SuccessFinalizer`` of every job kind, so it runs after the
    subprocess exited 0 and before the job is published as succeeded.
    """

    artifact_name = DEFAULT_METRICS_FILENAME
    component = "metrics"

    def __init__(self, jobs: JobManager, resolver: AnalysisSourceResolver | None = None):
        self.jobs = jobs
        self.resolver = resolver or AnalysisSourceResolver(jobs)
        self.registry = create_default_metric_registry()

    def __call__(self, job: JobRecord, *, output_path: Path | None = None) -> Path:
        stage = STAGE_BY_KIND.get(job.kind)
        if stage is None:
            raise ValueError(f"unsupported job kind for analysis metrics: {job.kind}")
        if not job.result_dir:
            raise ValueError(f"job {job.id} has no result directory")
        if not job.started_at or not job.finished_at:
            raise ValueError(f"job {job.id} has incomplete timestamps")

        context = MetricContext(
            self._source(job, stage),
            self._upstream_sources(job),
        )
        document = collect_metrics(context, self.registry, generated_at=job.finished_at)
        return write_metrics_document(
            output_path or Path(job.result_dir) / DEFAULT_METRICS_FILENAME,
            document,
        )

    def _upstream_sources(self, job: JobRecord) -> list[MetricSource]:
        sources: list[MetricSource] = []
        if job.kind == "tbfv":
            self._append_source(
                sources, job, "csc_job_id", "csc", "generated_from"
            )
        elif job.kind == "localization":
            tbfv_job = self._append_source(
                sources, job, "tbfv_job_id", "tbfv", "verified_from"
            )
            if tbfv_job is not None:
                self._append_source(
                    sources, tbfv_job, "csc_job_id", "csc", "generated_from"
                )
        return sources

    def _append_source(
        self,
        sources: list[MetricSource],
        job: JobRecord,
        config_key: str,
        stage: str,
        relation: str,
    ) -> JobRecord | None:
        record = self.resolver.upstream_record(job, config_key, stage)
        if record is None:
            return None
        sources.append(self._source(record, stage, relation=relation))
        return record

    def _source(
        self,
        job: JobRecord,
        stage: str,
        relation: str | None = None,
    ) -> MetricSource:
        return MetricSource(
            job_id=job.id,
            stage=stage,
            result_dir=self.resolver.result_dir(job),
            relation=relation,
            started_at=job.started_at,
            finished_at=job.finished_at,
        )
