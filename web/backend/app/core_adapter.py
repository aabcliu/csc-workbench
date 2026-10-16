from __future__ import annotations

import json
import os
import shutil
from collections.abc import Sequence
from pathlib import Path

from csc_engine.outcomes import DEFAULT_OUTCOME_FILENAME, OutcomeContractError
from csc_engine.metrics import DEFAULT_METRICS_FILENAME

from .analysis_outcomes import load_job_outcome
from .config import Settings
from .jobs import JobManager, SuccessFinalizer
from .models import (
    CSCJobRequest,
    JobRecord,
    JobStatus,
    LocalizationJobRequest,
    TBFVJobRequest,
)
from .outcome_policy import evaluate_next_stage
from .workspaces import WorkspaceStore
from csc_engine.java_exec import parse_top_level_md_def
from csc_engine.z3_helpers import validate_input_intervals


class CoreAdapter:
    def __init__(
        self,
        settings: Settings,
        workspaces: WorkspaceStore,
        jobs: JobManager,
        finalizers: Sequence[SuccessFinalizer] | None = None,
    ):
        self.settings = settings
        self.workspaces = workspaces
        self.jobs = jobs
        self.finalizers = finalizers

    def start_csc(self, request: CSCJobRequest) -> JobRecord:
        workspace = self.workspaces.get(request.workspace_id)
        # Validate against the same target method as the engine, before creating a job.
        if request.input_intervals is not None:
            source = self.workspaces.get_source(workspace.id)
            var_types = parse_top_level_md_def(source["java_source"])
            validate_input_intervals(
                {name: value.model_dump() for name, value in request.input_intervals.items()},
                var_types,
            )
        job = self.jobs.create("csc", workspace.id, request.model_dump())
        job_dir = self.jobs.job_dir(job.id)
        input_dir = job_dir / "input"
        self.workspaces.copy_inputs_to(workspace.id, input_dir)

        session = f"web_{job.id[:12]}"
        tmp_root = job_dir / "artifacts" / "csc"
        result_dir = tmp_root / session / workspace.class_name
        command = [
            self.settings.python_executable,
            str(self.settings.core_root / "csc_tool.py"),
            str(input_dir / workspace.java_filename),
            "--mode",
            request.mode,
            "--max-iter",
            str(request.max_iter),
            "--strategy",
            request.strategy,
            "--workers",
            str(request.workers),
            "--session",
            session,
            "--output",
            str(result_dir / "testcases.json"),
            "--keep-artifacts",
        ]
        if request.input_intervals is not None:
            command.extend([
                "--input-intervals",
                json.dumps(
                    {
                        name: interval.model_dump()
                        for name, interval in request.input_intervals.items()
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            ])
        elif request.range_bound is not None:
            command.extend(["--range-bound", str(request.range_bound)])
        if request.bootstrap:
            command.extend(["--bootstrap", request.bootstrap])
        if request.render_cct:
            command.extend(["--render-cct", "--cct-formats", "svg"])
        env = {
            "CSC_TMP_ROOT": str(tmp_root),
            "CSC_RUNNABLE_ROOT": str(job_dir / "runtime" / "runnable"),
        }
        env.update(self._java_env())
        return self.jobs.start(
            job.id,
            command,
            cwd=job_dir,
            env=env,
            result_dir=result_dir,
            finalizers=self.finalizers,
        )

    def start_tbfv(self, request: TBFVJobRequest) -> JobRecord:
        workspace = self.workspaces.get(request.workspace_id)
        source_job = self._successful_job(request.csc_job_id, "csc", workspace.id)
        self._require_outcome(source_job, "TBFV")
        if not workspace.fsf_filename:
            raise ValueError("Workspace has no FSF file")

        job = self.jobs.create("tbfv", workspace.id, request.model_dump())
        job_dir = self.jobs.job_dir(job.id)
        input_dir = job_dir / "input"
        self.workspaces.copy_inputs_to(workspace.id, input_dir)
        result_dir = job_dir / "artifacts" / "tbfv" / workspace.class_name
        shutil.copytree(
            Path(source_job.result_dir), result_dir,
            ignore=shutil.ignore_patterns(
                DEFAULT_OUTCOME_FILENAME,
                DEFAULT_METRICS_FILENAME,
                "folded_localization*.json",
                "cct_failure_localization*",
            ),
        )
        command = [
            self.settings.python_executable,
            str(self.settings.core_root / "refined_tbfv_tool.py"),
            "--csc-result-dir",
            str(result_dir),
            "--java-file",
            str(input_dir / workspace.java_filename),
            "--fsf",
            str(input_dir / workspace.fsf_filename),
            "--output",
            str(result_dir / "refined_tbfv_report.json"),
            "--fault-output-dir",
            str(result_dir),
        ]
        if request.render_cct:
            command.extend(["--render-cct", "--cct-formats", "svg"])
        return self.jobs.start(
            job.id,
            command,
            cwd=job_dir,
            env=self._java_env(),
            result_dir=result_dir,
            finalizers=self.finalizers,
        )

    def start_localization(self, request: LocalizationJobRequest) -> JobRecord:
        workspace = self.workspaces.get(request.workspace_id)
        source_job = self._successful_job(request.tbfv_job_id, "tbfv", workspace.id)
        self._require_outcome(source_job, "Localization")
        job = self.jobs.create("localization", workspace.id, request.model_dump())
        job_dir = self.jobs.job_dir(job.id)
        input_dir = job_dir / "input"
        self.workspaces.copy_inputs_to(workspace.id, input_dir)
        result_dir = job_dir / "artifacts" / "localization" / workspace.class_name
        shutil.copytree(
            Path(source_job.result_dir), result_dir,
            ignore=shutil.ignore_patterns(
                DEFAULT_OUTCOME_FILENAME,
                DEFAULT_METRICS_FILENAME,
                "folded_localization*.json",
                "cct_failure_localization*",
            ),
        )
        command = [
            self.settings.python_executable,
            str(Path(__file__).with_name("sbfl_runner.py")),
            "--core-root",
            str(self.settings.core_root),
            "--result-dir",
            str(result_dir),
            "--method",
            request.scoring_strategy,
        ]
        return self.jobs.start(
            job.id,
            command,
            cwd=job_dir,
            env=self._java_env(),
            result_dir=result_dir,
            finalizers=self.finalizers,
        )

    @staticmethod
    def _java_env() -> dict[str, str]:
        env: dict[str, str] = {}
        java_home = os.environ.get("JAVA_HOME")
        if not java_home:
            candidates = [
                Path("/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home"),
                Path("/opt/homebrew/Cellar/openjdk@17/17.0.16/libexec/openjdk.jdk/Contents/Home"),
                Path("/opt/homebrew/opt/openjdk/libexec/openjdk.jdk/Contents/Home"),
                Path("/opt/homebrew/Cellar/openjdk/23.0.2/libexec/openjdk.jdk/Contents/Home"),
                Path("/Library/Java/JavaVirtualMachines/temurin-17.jdk/Contents/Home"),
            ]
            for candidate in candidates:
                if (candidate / "bin" / "javac").is_file():
                    java_home = str(candidate)
                    break
        if java_home:
            env["JAVA_HOME"] = java_home
            current_path = os.environ.get("PATH", "")
            bin_dir = str(Path(java_home) / "bin")
            if bin_dir not in current_path.split(":"):
                env["PATH"] = f"{bin_dir}:{current_path}"
        return env

    def _successful_job(
        self,
        job_id: str,
        kind: str,
        workspace_id: str,
    ) -> JobRecord:
        job = self.jobs.get(job_id)
        if job.kind != kind:
            raise ValueError(f"Expected a {kind} job")
        if job.workspace_id != workspace_id:
            raise ValueError(f"Source job belongs to another workspace: {job_id}")
        if job.status is not JobStatus.succeeded:
            raise ValueError(f"Source job has not succeeded: {job_id}")
        if getattr(job, "archive", None) is not None:
            raise ValueError(
                "Archived results are read-only; rerun the upstream stage to create a live result"
            )
        if not job.result_dir:
            raise ValueError(f"Source job result directory is missing: {job_id}")
        job_root = self.jobs.job_dir(job.id).resolve()
        result_dir = Path(job.result_dir).resolve()
        if job_root not in result_dir.parents or not result_dir.is_dir():
            raise ValueError(f"Source job result directory is unsafe: {job_id}")
        return job

    def _require_outcome(self, source_job: JobRecord, downstream_label: str) -> None:
        """Gate a downstream start on the frozen OutcomePolicy rule."""
        try:
            document = load_job_outcome(self.jobs, source_job)
        except KeyError:
            raise ValueError(
                f"{downstream_label} is not available: the upstream Analysis "
                "Outcome is missing (source_artifact_missing)"
            ) from None
        except OutcomeContractError:
            raise ValueError(
                f"{downstream_label} is not available: the upstream Analysis "
                "Outcome is invalid (source_artifact_invalid)"
            ) from None
        decision = evaluate_next_stage(source_job.kind, document)
        if not decision.available:
            codes = ", ".join(decision.reason_codes) or "unknown"
            raise ValueError(f"{downstream_label} is not available: {codes}")
