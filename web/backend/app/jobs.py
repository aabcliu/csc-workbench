from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

from csc_engine.metrics import DEFAULT_METRICS_FILENAME
from csc_engine.outcomes import DEFAULT_OUTCOME_FILENAME

from .models import (
    ArtifactRecord,
    FinalizationWarning,
    JobRecord,
    JobStatus,
    media_type_for,
    utc_now,
)


_INTERNAL_LOCALIZATION_NAMES = {
    "folded_localization.json",
    "folded_localization_aggregated.json",
    "cct_failure_localization.json",
}


def is_internal_localization_artifact(path: Path) -> bool:
    """Keep retained research-only localization artifacts out of public surfaces."""
    return (
        path.name in _INTERNAL_LOCALIZATION_NAMES
        or path.name.startswith("cct_failure_localization_")
    )


class SuccessFinalizer(Protocol):
    """Hook run after a job exits 0 and before it is published as succeeded.

    Receives the job record carrying ``return_code`` and the ``finished_at``
    that is about to be persisted. A returned path is removed when the job is
    cancelled while the hook was still running.
    """

    def __call__(self, job: JobRecord) -> Path | None: ...


class JobManager:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._processes: dict[str, subprocess.Popen[str]] = {}

    def list(self) -> list[JobRecord]:
        with self._lock:
            jobs: list[JobRecord] = []
            for path in sorted(self.root.glob("*/job.json")):
                try:
                    jobs.append(
                        JobRecord.model_validate_json(path.read_text(encoding="utf-8"))
                    )
                except (OSError, ValueError):
                    continue
            return sorted(jobs, key=lambda item: item.created_at, reverse=True)

    def list_for_ids(self, job_ids: list[str]) -> list[JobRecord]:
        wanted = set(job_ids)
        records = {job.id: job for job in self.list() if job.id in wanted}
        return [records[job_id] for job_id in job_ids]

    def get(self, job_id: str) -> JobRecord:
        # Mounted volumes can expose incomplete state during rename/flush.
        # Readers join the writer's publication boundary rather than relying
        # solely on filesystem rename semantics.
        with self._lock:
            path = self.job_dir(job_id) / "job.json"
            if not path.is_file():
                raise KeyError(job_id)
            return JobRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def create(
        self,
        kind: str,
        workspace_id: str,
        config: dict[str, Any],
    ) -> JobRecord:
        job = JobRecord(
            id=uuid.uuid4().hex,
            kind=kind,
            workspace_id=workspace_id,
            config=config,
        )
        self.job_dir(job.id).mkdir(parents=True)
        self._write(job)
        return job

    def start(
        self,
        job_id: str,
        command: list[str],
        cwd: Path,
        env: dict[str, str] | None = None,
        result_dir: Path | None = None,
        finalizers: Sequence[SuccessFinalizer] | None = None,
    ) -> JobRecord:
        with self._lock:
            job = self.get(job_id)
            if job.status is not JobStatus.pending:
                raise ValueError(f"Job is not pending: {job_id}")
            job.command = command
            job.result_dir = str(result_dir.resolve()) if result_dir else None
            self._write(job)
            thread = threading.Thread(
                target=self._run,
                args=(job_id, command, cwd, env, finalizers),
                daemon=True,
                name=f"csc-job-{job_id[:8]}",
            )
            thread.start()
            return self.get(job_id)

    def cancel(self, job_id: str) -> JobRecord:
        with self._lock:
            job = self.get(job_id)
            if job.status not in {JobStatus.pending, JobStatus.running}:
                return job
            process = self._processes.get(job_id)
            if process is not None and process.poll() is None:
                process.terminate()
            job.status = JobStatus.cancelled
            job.finished_at = utc_now()
            self._write(job)
            return job

    def read_log(self, job_id: str, offset: int = 0) -> tuple[str, int]:
        path = self.job_dir(job_id) / "job.log"
        if not path.exists():
            return "", 0
        size = path.stat().st_size
        safe_offset = max(0, min(offset, size))
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(safe_offset)
            return handle.read(), size

    def artifacts(self, job_id: str) -> list[ArtifactRecord]:
        job_dir = self.job_dir(job_id)
        job = self.get(job_id)
        records: list[ArtifactRecord] = []
        for path in sorted(job_dir.rglob("*")):
            if not path.is_file() or path.name == "job.json" or ".finalization" in path.parts:
                continue
            if is_internal_localization_artifact(path):
                continue
            if path.name in {DEFAULT_METRICS_FILENAME, DEFAULT_OUTCOME_FILENAME} and job.status is not JobStatus.succeeded:
                continue
            relative = path.relative_to(job_dir).as_posix()
            records.append(
                ArtifactRecord(
                    path=relative,
                    name=path.name,
                    size=path.stat().st_size,
                    media_type=media_type_for(path),
                )
            )
        return records

    def artifact_path(self, job_id: str, relative_path: str) -> Path:
        root = self.job_dir(job_id).resolve()
        candidate = (root / relative_path).resolve()
        if ".finalization" in Path(relative_path).parts or root not in candidate.parents or not candidate.is_file() or is_internal_localization_artifact(candidate):
            raise KeyError(relative_path)
        if ".finalization" in candidate.relative_to(root).parts:
            raise KeyError(relative_path)
        if candidate.name in {DEFAULT_METRICS_FILENAME, DEFAULT_OUTCOME_FILENAME} and self.get(job_id).status is not JobStatus.succeeded:
            raise KeyError(relative_path)
        return candidate

    def cct_snapshot_path(self, job_id: str) -> Path:
        """Resolve the stable CCT snapshot artifact for a job."""
        job = self.get(job_id)
        if not job.result_dir:
            raise KeyError("cct.snapshot.json")
        job_root = self.job_dir(job_id).resolve()
        result_root = Path(job.result_dir).resolve()
        candidate = (result_root / "cct.snapshot.json").resolve()
        if (
            job_root not in candidate.parents
            or result_root not in candidate.parents
            or not candidate.is_file()
        ):
            raise KeyError("cct.snapshot.json")
        return candidate

    def metrics_path(self, job_id: str) -> Path:
        """Resolve the stable Analysis Metrics artifact for a job."""
        job = self.get(job_id)
        if not job.result_dir:
            raise KeyError(DEFAULT_METRICS_FILENAME)
        job_root = self.job_dir(job_id).resolve()
        result_root = Path(job.result_dir).resolve()
        candidate = (result_root / DEFAULT_METRICS_FILENAME).resolve()
        if (
            job_root not in candidate.parents
            or result_root not in candidate.parents
            or not candidate.is_file()
        ):
            raise KeyError(DEFAULT_METRICS_FILENAME)
        return candidate

    def outcome_path(self, job_id: str) -> Path:
        """Resolve the stable Analysis Outcome artifact for a job."""
        job = self.get(job_id)
        if not job.result_dir:
            raise KeyError(DEFAULT_OUTCOME_FILENAME)
        job_root = self.job_dir(job_id).resolve()
        result_root = Path(job.result_dir).resolve()
        candidate = (result_root / DEFAULT_OUTCOME_FILENAME).resolve()
        if (
            job_root not in candidate.parents
            or result_root not in candidate.parents
            or not candidate.is_file()
        ):
            raise KeyError(DEFAULT_OUTCOME_FILENAME)
        return candidate

    def job_dir(self, job_id: str) -> Path:
        if not all(char in "0123456789abcdef" for char in job_id) or len(job_id) != 32:
            raise KeyError(job_id)
        return self.root / job_id

    def _run(
        self,
        job_id: str,
        command: list[str],
        cwd: Path,
        env: dict[str, str] | None,
        finalizers: Sequence[SuccessFinalizer] | None,
    ) -> None:
        log_path = self.job_dir(job_id) / "job.log"
        try:
            with self._lock:
                job = self.get(job_id)
                if job.status is JobStatus.cancelled:
                    return
                job.status = JobStatus.running
                job.started_at = utc_now()
                self._write(job)

            process_env = os.environ.copy()
            if env:
                process_env.update(env)
            cwd.mkdir(parents=True, exist_ok=True)
            with log_path.open("w", encoding="utf-8") as log:
                process = subprocess.Popen(
                    command,
                    cwd=cwd,
                    env=process_env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                with self._lock:
                    self._processes[job_id] = process
                    if self.get(job_id).status is JobStatus.cancelled and process.poll() is None:
                        process.terminate()
                return_code = process.wait()

            finished_at = utc_now()
            finalizer_artifacts: list[Path] = []
            staged_artifacts: list[tuple[Path, Path]] = []
            staging = self.job_dir(job_id) / ".finalization"
            finalization_warnings: list[FinalizationWarning] = []
            if return_code == 0 and finalizers:
                with self._lock:
                    job = self.get(job_id)
                    cancelled = job.status is JobStatus.cancelled
                    completed = job.model_copy(
                        update={"return_code": return_code, "finished_at": finished_at}
                    )
                if not cancelled:
                    for finalizer in finalizers:
                        with self._lock:
                            if self.get(job_id).status is JobStatus.cancelled:
                                break
                        output_path = None
                        try:
                            name = getattr(finalizer, "artifact_name", None)
                            if name in {DEFAULT_METRICS_FILENAME, DEFAULT_OUTCOME_FILENAME}:
                                if not completed.result_dir:
                                    raise ValueError(f"job {job_id} has no result directory")
                                root = Path(completed.result_dir).resolve()
                                if self.job_dir(job_id).resolve() not in root.parents:
                                    raise ValueError("finalizer result directory is outside the job")
                                if staging.resolve().parent != self.job_dir(job_id).resolve() or staging.is_symlink():
                                    raise ValueError("unsafe finalization staging directory")
                                staging.mkdir(exist_ok=True)
                                output_path = staging / name
                                artifact = finalizer(completed, output_path=output_path)
                                if artifact != output_path or not output_path.is_file():
                                    raise ValueError("finalizer did not produce its declared artifact")
                                staged_artifacts.append((output_path, root / name))
                            else:
                                artifact = finalizer(completed)
                        except Exception as exc:  # A failed add-on must not fail the job.
                            if output_path is not None:
                                output_path.unlink(missing_ok=True)
                            self._append_finalizer_warning(log_path, exc)
                            finalization_warnings.append(
                                FinalizationWarning(
                                    component=getattr(
                                        finalizer, "component", type(finalizer).__name__
                                    ),
                                    code="finalizer_failed",
                                    message=str(exc),
                                )
                            )
                        else:
                            if artifact is not None and output_path is None:
                                finalizer_artifacts.append(artifact)

            with self._lock:
                job = self.get(job_id)
                if job.status is JobStatus.cancelled:
                    discard = finalizer_artifacts
                else:
                    discard = []
                    # The job record is the publication boundary. Cancel shares
                    # this lock; readers only consume these artifacts after success.
                    for temporary, destination in staged_artifacts:
                        temporary.replace(destination)
                    for directory in {p.parent for _, p in staged_artifacts}:
                        self._sync_directory(directory)
                    job.return_code = return_code
                    job.status = (
                        JobStatus.succeeded
                        if return_code == 0
                        else JobStatus.failed
                    )
                    if return_code != 0:
                        job.error = f"Command exited with code {return_code}"
                    job.finished_at = finished_at
                    job.finalization_warnings = finalization_warnings
                    self._write(job)
            for artifact in discard:
                artifact.unlink(missing_ok=True)
            shutil.rmtree(staging, ignore_errors=True)
        except Exception as exc:
            with self._lock:
                job = self.get(job_id)
                if job.status is not JobStatus.cancelled:
                    job.status = JobStatus.failed
                    job.error = str(exc)
                    job.finished_at = utc_now()
                    self._write(job)
        finally:
            with self._lock:
                record = self.get(job_id)
                if record.status is not JobStatus.succeeded:
                    self._discard_derived(record)
                shutil.rmtree(self.job_dir(job_id) / ".finalization", ignore_errors=True)
                self._processes.pop(job_id, None)

    def _discard_derived(self, job: JobRecord) -> None:
        if not job.result_dir:
            return
        root = Path(job.result_dir).resolve()
        if self.job_dir(job.id).resolve() not in root.parents:
            return
        for name in (DEFAULT_METRICS_FILENAME, DEFAULT_OUTCOME_FILENAME):
            path = root / name
            if path.is_symlink() or path.is_file():
                path.unlink(missing_ok=True)

    def recover_interrupted_jobs(self) -> None:
        # A runtime root has one backend owner. Never replay analysis on restart.
        for job in self.list():
            if job.status in {JobStatus.pending, JobStatus.running}:
                self._discard_derived(job)
                job.status = JobStatus.failed
                job.error = "Backend stopped before the job was published; rerun the analysis."
                job.finished_at = utc_now()
                self._write(job)
            elif job.status in {JobStatus.cancelled, JobStatus.failed}:
                self._discard_derived(job)
            shutil.rmtree(self.job_dir(job.id) / ".finalization", ignore_errors=True)

    def _append_finalizer_warning(self, log_path: Path, error: Exception) -> None:
        record = {
            "event": "finalizer_warning",
            "error": f"{type(error).__name__}: {error}",
        }
        try:
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write("\n" + json.dumps(record) + "\n")
        except OSError:
            pass

    @staticmethod
    def _sync_directory(directory: Path) -> None:
        # Directory fsync is available on the supported POSIX runtime.
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _write(self, job: JobRecord) -> None:
        with self._lock:
            path = self.job_dir(job.id) / "job.json"
            temporary = path.with_suffix(".tmp")
            with temporary.open("w", encoding="utf-8") as handle:
                handle.write(json.dumps(job.model_dump(mode="json"), indent=2) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(path)
            self._sync_directory(path.parent)
