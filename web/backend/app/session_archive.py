"""Portable, read-only session archives for completed analysis lineages."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import stat
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from csc_engine.cct_snapshot import CCTSnapshotError, validate_cct_snapshot
from csc_engine.metrics import MetricsContractError, load_metrics_document
from csc_engine.outcomes import OutcomeContractError, load_outcome_document
from csc_engine.sbfl import DEFAULT_SBFL_FILENAME, load_sbfl_report

from .analysis_sources import AnalysisSourceResolver
from .evidence import EvidenceService, EvidenceUnavailable
from .jobs import JobManager, is_internal_localization_artifact
from .models import ArchiveOrigin, JobRecord, JobStatus, WorkspaceRecord
from .version import BUILD_REVISION, SESSION_FORMAT, SESSION_VERSION, TOOL_NAME, TOOL_VERSION
from .workspaces import WorkspaceStore


MANIFEST_NAME = "session.json"
ARCHIVE_SUFFIX = ".csc-session.zip"
MAX_ARCHIVE_FILES = 50_000
MAX_ARCHIVE_UNCOMPRESSED = 512 * 1024 * 1024
MAX_ARCHIVE_FILE = 64 * 1024 * 1024
MAX_COMPRESSION_RATIO = 250
_JOB_KINDS = {"csc", "tbfv", "localization"}
_STRUCTURED_SUFFIXES = {".json", ".jsonl", ".txt"}
_ABSOLUTE_WINDOWS = re.compile(r"^[A-Za-z]:[\\/]")


class SessionArchiveError(ValueError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _safe_member(name: str) -> PurePosixPath:
    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
        raise SessionArchiveError("Archive contains an unsafe member name")
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or path.as_posix() != name
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise SessionArchiveError("Archive contains an unsafe member path")
    return path


def _sanitize_json(value: Any, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {item: _sanitize_json(child, item) for item, child in value.items()}
    if isinstance(value, list):
        return [_sanitize_json(child, key) for child in value]
    if isinstance(value, str):
        if key == "trace_path":
            normalized = value.replace("\\", "/")
            marker = "/traces/"
            if marker in normalized:
                return "traces/" + normalized.split(marker, 1)[1]
        if value.startswith("/") or _ABSOLUTE_WINDOWS.match(value):
            basename = value.replace("\\", "/").rsplit("/", 1)[-1]
            if key in {"source_file", "fsf_file"}:
                return f"input/{basename}"
            if key == "source_report":
                return basename
            return f"archive://redacted/{basename}"
    return value


def _portable_content(path: Path) -> bytes:
    if path.suffix == ".json":
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise SessionArchiveError(f"Invalid JSON artifact: {path.name}") from exc
        return json.dumps(
            _sanitize_json(value), ensure_ascii=False, allow_nan=False, indent=2
        ).encode("utf-8") + b"\n"
    if path.suffix == ".jsonl":
        output: list[str] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    output.append(json.dumps(_sanitize_json(json.loads(line)), ensure_ascii=False, allow_nan=False))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise SessionArchiveError(f"Invalid JSONL artifact: {path.name}") from exc
        return (("\n".join(output) + "\n") if output else "").encode("utf-8")
    try:
        content = path.read_bytes()
        content.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise SessionArchiveError(f"Invalid text artifact: {path.name}") from exc
    return content


class SessionArchiveService:
    def __init__(self, runtime_root: Path, workspaces: WorkspaceStore, jobs: JobManager):
        self.runtime_root = runtime_root.resolve()
        self.workspaces = workspaces
        self.jobs = jobs
        self.resolver = AnalysisSourceResolver(jobs)

    def export(self, job_id: str, destination: Path) -> tuple[Path, dict[str, Any]]:
        try:
            root_job = self.jobs.get(job_id)
        except KeyError as exc:
            raise SessionArchiveError("Historical run not found") from exc
        if root_job.status is not JobStatus.succeeded:
            raise SessionArchiveError("Only succeeded runs can be exported")
        try:
            workspace = self.workspaces.get(root_job.workspace_id)
        except KeyError as exc:
            raise SessionArchiveError("The program input for this run is unavailable") from exc
        lineage = self._lineage(root_job)
        entries: dict[str, bytes] = {}
        workspace_input = self.workspaces.root / workspace.id / "input"
        workspace_root = (self.workspaces.root / workspace.id).resolve()
        if self.workspaces.root.resolve() not in workspace_root.parents:
            raise SessionArchiveError("Workspace input directory is unsafe")
        for filename in (workspace.java_filename, workspace.fsf_filename):
            if filename:
                source = workspace_input / filename
                resolved_source = source.resolve()
                if (
                    not source.is_file()
                    or source.is_symlink()
                    or workspace_root not in resolved_source.parents
                ):
                    raise SessionArchiveError(f"Workspace input is unavailable: {filename}")
                content = source.read_bytes()
                if len(content) > MAX_ARCHIVE_FILE:
                    raise SessionArchiveError(f"Workspace input exceeds archive member limit: {filename}")
                entries[f"workspace/input/{filename}"] = content

        job_records = []
        for job in lineage:
            root = self.resolver.result_dir(job)
            result_count = 0
            for source in sorted(root.rglob("*")):
                if not source.is_file() or source.is_symlink():
                    continue
                if is_internal_localization_artifact(source):
                    continue
                if source.suffix.lower() not in _STRUCTURED_SUFFIXES or source.name == "run_log.jsonl":
                    continue
                relative = source.relative_to(root)
                if any(part.startswith(".") for part in relative.parts):
                    continue
                content = _portable_content(source)
                if len(content) > MAX_ARCHIVE_FILE:
                    raise SessionArchiveError(f"Artifact exceeds archive member limit: {relative}")
                entries[f"jobs/{job.id}/result/{relative.as_posix()}"] = content
                result_count += 1
            if not result_count:
                raise SessionArchiveError(f"Run has no portable result artifacts: {job.id}")
            job_records.append(self._portable_job(job))

        if len(entries) + 1 > MAX_ARCHIVE_FILES:
            raise SessionArchiveError("Portable session contains too many files")
        if sum(len(content) for content in entries.values()) > MAX_ARCHIVE_UNCOMPRESSED:
            raise SessionArchiveError("Portable session exceeds the uncompressed size limit")

        files = [
            {"path": name, "size": len(content), "sha256": _sha256(content)}
            for name, content in sorted(entries.items())
        ]
        workspace_record = workspace.model_dump(mode="json", exclude={"archive"})
        semantic = {
            "format": SESSION_FORMAT,
            "version": SESSION_VERSION,
            "rootJobId": root_job.id,
            "workspace": workspace_record,
            "jobs": job_records,
            "files": files,
        }
        session_id = _sha256(_canonical(semantic))
        manifest = {
            **semantic,
            "sessionId": session_id,
            "exportedAt": _utc_now(),
            "producer": {
                "name": TOOL_NAME,
                "version": TOOL_VERSION,
                "buildRevision": BUILD_REVISION,
                "python": platform.python_version(),
                "platform": platform.system().lower(),
            },
            "pathPolicy": "absolute paths removed; trace paths normalized relative to result roots",
        }
        self._validate_manifest(manifest)
        validated_jobs = [
            self._manifest_job(record, workspace.id) for record in manifest["jobs"]
        ]
        self._validate_lineage(validated_jobs, root_job.id)
        manifest_content = json.dumps(
            manifest, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2
        ).encode("utf-8") + b"\n"
        if len(manifest_content) > MAX_ARCHIVE_FILE:
            raise SessionArchiveError("Portable session manifest exceeds the member size limit")
        if sum(len(content) for content in entries.values()) + len(manifest_content) > MAX_ARCHIVE_UNCOMPRESSED:
            raise SessionArchiveError("Portable session exceeds the uncompressed size limit")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            self._write_zip(archive, MANIFEST_NAME, manifest_content)
            for name, content in sorted(entries.items()):
                self._write_zip(archive, name, content)
        return destination, manifest

    def import_archive(self, source: Path) -> dict[str, Any]:
        staging = Path(tempfile.mkdtemp(prefix=".session-import-", dir=self.runtime_root))
        installed: list[Path] = []
        try:
            with zipfile.ZipFile(source) as archive:
                manifest, contents = self._read_and_validate(archive)
            workspace = WorkspaceRecord.model_validate(manifest["workspace"])
            jobs = [self._manifest_job(record, workspace.id) for record in manifest["jobs"]]
            root_job_id = manifest["rootJobId"]
            if root_job_id not in {job.id for job in jobs}:
                raise SessionArchiveError("Archive root job is absent from its lineage")
            self._validate_lineage(jobs, root_job_id)
            final_workspace = self.workspaces.root / workspace.id
            final_jobs = {job.id: self.jobs.job_dir(job.id) for job in jobs}
            collisions = [path for path in [final_workspace, *final_jobs.values()] if path.exists()]
            if collisions:
                raise SessionArchiveError("Archive IDs already exist in this runtime; existing data was not overwritten")

            origin = ArchiveOrigin(
                session_id=manifest["sessionId"],
                root_job_id=root_job_id,
                producer_version=manifest["producer"]["version"],
            )
            staged_workspace = staging / "workspaces" / workspace.id
            staged_input = staged_workspace / "input"
            staged_input.mkdir(parents=True)
            for filename in (workspace.java_filename, workspace.fsf_filename):
                if filename:
                    archive_name = f"workspace/input/{filename}"
                    if archive_name not in contents:
                        raise SessionArchiveError(f"Archive input is missing: {filename}")
                    (staged_input / filename).write_bytes(contents[archive_name])
            workspace.archive = origin
            (staged_workspace / "workspace.json").write_text(
                json.dumps(workspace.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8"
            )

            for job in jobs:
                staged_job = staging / "jobs" / job.id
                result = staged_job / "artifacts" / job.kind / workspace.class_name
                result.mkdir(parents=True)
                prefix = f"jobs/{job.id}/result/"
                for name, content in contents.items():
                    if name.startswith(prefix):
                        relative = _safe_member(name[len(prefix):])
                        target = result.joinpath(*relative.parts)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(content)
                shutil.copytree(staged_input, staged_job / "input")
                job.archive = origin
                job.command = []
                job.result_dir = str((final_jobs[job.id] / "artifacts" / job.kind / workspace.class_name).resolve())
                (staged_job / "job.json").write_text(
                    json.dumps(job.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8"
                )
                self._validate_result(result, job)

            final_workspace.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged_workspace, final_workspace)
            installed.append(final_workspace)
            for job in jobs:
                final = final_jobs[job.id]
                final.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staging / "jobs" / job.id, final)
                installed.append(final)
            self._validate_installed_evidence(jobs[-1])
            root_job = self.jobs.get(root_job_id)
            return {
                "sessionId": manifest["sessionId"],
                "rootJobId": root_job_id,
                "workspace": self.workspaces.get(workspace.id),
                "job": root_job,
                "jobs": self.jobs.list_for_ids([job.id for job in jobs]),
                "producer": manifest["producer"],
            }
        except (
            zipfile.BadZipFile,
            OSError,
            KeyError,
            ValueError,
            MetricsContractError,
            OutcomeContractError,
            CCTSnapshotError,
            EvidenceUnavailable,
            TypeError,
            AttributeError,
        ) as exc:
            for path in reversed(installed):
                if path.exists():
                    shutil.rmtree(path)
            if isinstance(exc, SessionArchiveError):
                raise
            raise SessionArchiveError(f"Session archive is invalid: {exc}") from exc
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def _lineage(self, root: JobRecord) -> list[JobRecord]:
        lineage = [root]
        current = root
        while current.kind != "csc":
            if current.kind == "localization":
                key, kind = "tbfv_job_id", "tbfv"
            elif current.kind == "tbfv":
                key, kind = "csc_job_id", "csc"
            else:
                raise SessionArchiveError(f"Unsupported job kind: {current.kind}")
            upstream = self.resolver.upstream_record(current, key, kind)
            if upstream is None:
                raise SessionArchiveError("The selected run has incomplete or invalid lineage")
            lineage.append(upstream)
            current = upstream
        lineage.reverse()
        return lineage

    @staticmethod
    def _portable_job(job: JobRecord) -> dict[str, Any]:
        return {
            "id": job.id,
            "kind": job.kind,
            "workspaceId": job.workspace_id,
            "status": job.status.value,
            "createdAt": job.created_at,
            "startedAt": job.started_at,
            "finishedAt": job.finished_at,
            "returnCode": job.return_code,
            "config": _sanitize_json(job.config),
            "finalizationWarnings": _sanitize_json(
                [warning.model_dump(mode="json") for warning in job.finalization_warnings]
            ),
        }

    @staticmethod
    def _write_zip(archive: zipfile.ZipFile, name: str, content: bytes) -> None:
        info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o100644 << 16
        archive.writestr(info, content)

    def _read_and_validate(self, archive: zipfile.ZipFile) -> tuple[dict[str, Any], dict[str, bytes]]:
        infos = archive.infolist()
        if len(infos) > MAX_ARCHIVE_FILES:
            raise SessionArchiveError("Archive contains too many files")
        seen: set[str] = set()
        total = 0
        for info in infos:
            path = _safe_member(info.filename)
            if info.filename in seen or info.is_dir():
                raise SessionArchiveError("Archive contains duplicate or directory members")
            seen.add(info.filename)
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise SessionArchiveError("Archive symbolic links are not allowed")
            if info.flag_bits & 0x1:
                raise SessionArchiveError("Encrypted archives are not supported")
            if info.file_size > MAX_ARCHIVE_FILE:
                raise SessionArchiveError(f"Archive member is too large: {path}")
            total += info.file_size
            if total > MAX_ARCHIVE_UNCOMPRESSED:
                raise SessionArchiveError("Archive uncompressed size exceeds the limit")
            if info.compress_size and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO:
                raise SessionArchiveError("Archive compression ratio exceeds the safety limit")
        if MANIFEST_NAME not in seen:
            raise SessionArchiveError("Archive manifest is missing")
        try:
            manifest = json.loads(archive.read(MANIFEST_NAME))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise SessionArchiveError("Archive manifest is invalid JSON") from exc
        self._validate_manifest(manifest)
        expected = {record["path"]: record for record in manifest["files"]}
        if seen != {MANIFEST_NAME, *expected}:
            raise SessionArchiveError("Archive members do not match the manifest")
        contents = {}
        for name, record in expected.items():
            content = archive.read(name)
            if len(content) != record["size"] or _sha256(content) != record["sha256"]:
                raise SessionArchiveError(f"Archive checksum mismatch: {name}")
            contents[name] = content
        semantic = {key: manifest[key] for key in ("format", "version", "rootJobId", "workspace", "jobs", "files")}
        if _sha256(_canonical(semantic)) != manifest["sessionId"]:
            raise SessionArchiveError("Archive session fingerprint does not match its content")
        return manifest, contents

    @staticmethod
    def _validate_manifest(manifest: Any) -> None:
        if not isinstance(manifest, dict):
            raise SessionArchiveError("Archive manifest must be an object")
        required = {"format", "version", "sessionId", "exportedAt", "producer", "rootJobId", "workspace", "jobs", "files", "pathPolicy"}
        if set(manifest) != required:
            raise SessionArchiveError("Archive manifest fields do not match version 1")
        if manifest["format"] != SESSION_FORMAT or type(manifest["version"]) is not int or manifest["version"] != SESSION_VERSION:
            raise SessionArchiveError("Unsupported session archive format or version")
        if not re.fullmatch(r"[a-f0-9]{64}", manifest["sessionId"] or ""):
            raise SessionArchiveError("Invalid session fingerprint")
        if not re.fullmatch(r"[a-f0-9]{32}", manifest["rootJobId"] or ""):
            raise SessionArchiveError("Invalid root job ID")
        producer = manifest["producer"]
        if (
            not isinstance(producer, dict)
            or set(producer) != {"name", "version", "buildRevision", "python", "platform"}
            or any(not isinstance(producer[field], str) or not producer[field] for field in ("name", "version", "python", "platform"))
            or (producer["buildRevision"] is not None and not isinstance(producer["buildRevision"], str))
        ):
            raise SessionArchiveError("Invalid producer metadata")
        if not isinstance(manifest["exportedAt"], str) or not manifest["exportedAt"]:
            raise SessionArchiveError("Invalid export timestamp")
        if not isinstance(manifest["pathPolicy"], str) or not manifest["pathPolicy"]:
            raise SessionArchiveError("Invalid path policy")
        workspace = manifest["workspace"]
        workspace_fields = {
            "id", "name", "class_name", "java_filename", "fsf_filename", "created_at"
        }
        if not isinstance(workspace, dict) or set(workspace) != workspace_fields:
            raise SessionArchiveError("Invalid archived workspace record")
        if not re.fullmatch(r"[a-f0-9]{32}", workspace.get("id") or ""):
            raise SessionArchiveError("Invalid archived workspace ID")
        if not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", workspace.get("class_name") or ""):
            raise SessionArchiveError("Invalid archived Java class name")
        filenames = [workspace.get("java_filename")]
        if workspace.get("fsf_filename") is not None:
            filenames.append(workspace["fsf_filename"])
        if (
            not isinstance(workspace.get("name"), str)
            or not isinstance(workspace.get("created_at"), str)
            or not workspace.get("created_at")
            or any(not isinstance(name, str) or PurePosixPath(name).name != name for name in filenames)
            or not workspace["java_filename"].lower().endswith(".java")
            or (workspace.get("fsf_filename") and not workspace["fsf_filename"].lower().endswith(".txt"))
        ):
            raise SessionArchiveError("Invalid archived workspace metadata")
        if not isinstance(manifest["jobs"], list) or not 1 <= len(manifest["jobs"]) <= 3:
            raise SessionArchiveError("Archive must contain one to three lineage jobs")
        if not isinstance(manifest["files"], list) or not manifest["files"]:
            raise SessionArchiveError("Archive file manifest is empty")
        paths: set[str] = set()
        job_ids = {
            record.get("id") for record in manifest["jobs"] if isinstance(record, dict)
        }
        for record in manifest["files"]:
            if not isinstance(record, dict) or set(record) != {"path", "size", "sha256"}:
                raise SessionArchiveError("Invalid archive file record")
            _safe_member(record["path"])
            if record["path"] in paths or type(record["size"]) is not int or record["size"] < 0 or not re.fullmatch(r"[a-f0-9]{64}", record["sha256"] or ""):
                raise SessionArchiveError("Invalid or duplicate archive file record")
            path = PurePosixPath(record["path"])
            valid_workspace = path.parts[:2] == ("workspace", "input") and len(path.parts) == 3
            valid_job = (
                len(path.parts) >= 4
                and path.parts[0] == "jobs"
                and path.parts[1] in job_ids
                and path.parts[2] == "result"
                and path.suffix.lower() in _STRUCTURED_SUFFIXES
                and path.name != "run_log.jsonl"
                and not any(part.startswith(".") for part in path.parts[3:])
            )
            if not (valid_workspace or valid_job):
                raise SessionArchiveError("Archive file is outside the portable session layout")
            paths.add(record["path"])
        expected_inputs = {f"workspace/input/{name}" for name in filenames}
        for path in expected_inputs:
            _safe_member(path)
        actual_inputs = {path for path in paths if path.startswith("workspace/input/")}
        if actual_inputs != expected_inputs:
            raise SessionArchiveError("Archive workspace inputs do not match its metadata")
        for job_id in job_ids:
            prefix = f"jobs/{job_id}/result/"
            if not any(path.startswith(prefix) for path in paths):
                raise SessionArchiveError(f"Archived job has no result files: {job_id}")

    @staticmethod
    def _manifest_job(record: Any, workspace_id: str) -> JobRecord:
        if not isinstance(record, dict) or set(record) != {
            "id", "kind", "workspaceId", "status", "createdAt", "startedAt", "finishedAt",
            "returnCode", "config", "finalizationWarnings",
        }:
            raise SessionArchiveError("Invalid archived job record")
        if record["kind"] not in _JOB_KINDS or record["workspaceId"] != workspace_id or record["status"] != "succeeded":
            raise SessionArchiveError("Archived jobs must be succeeded lineage peers")
        if not re.fullmatch(r"[a-f0-9]{32}", record["id"] or ""):
            raise SessionArchiveError("Archived job ID is invalid")
        if (
            any(not isinstance(record[field], str) or not record[field] for field in ("createdAt", "startedAt", "finishedAt"))
            or type(record["returnCode"]) is not int
            or record["returnCode"] != 0
            or not isinstance(record["config"], dict)
            or not isinstance(record["finalizationWarnings"], list)
        ):
            raise SessionArchiveError("Archived job metadata is invalid")
        return JobRecord(
            id=record["id"], kind=record["kind"], workspace_id=workspace_id,
            status=JobStatus.succeeded, created_at=record["createdAt"], started_at=record["startedAt"],
            finished_at=record["finishedAt"], return_code=record["returnCode"], config=record["config"],
            finalization_warnings=record["finalizationWarnings"],
        )

    @staticmethod
    def _validate_lineage(jobs: list[JobRecord], root_job_id: str) -> None:
        if len({job.id for job in jobs}) != len(jobs):
            raise SessionArchiveError("Archive contains duplicate job IDs")
        expected = ["csc", "tbfv", "localization"][: len(jobs)]
        if [job.kind for job in jobs] != expected or jobs[-1].id != root_job_id:
            raise SessionArchiveError("Archive lineage order does not match its root stage")
        for index, job in enumerate(jobs):
            if job.config.get("workspace_id") not in {None, job.workspace_id}:
                raise SessionArchiveError("Archived job config references another workspace")
            if index and job.config.get("csc_job_id" if job.kind == "tbfv" else "tbfv_job_id") != jobs[index - 1].id:
                raise SessionArchiveError("Archived job lineage references another job")

    @staticmethod
    def _validate_result(result: Path, job: JobRecord) -> None:
        outcome = result / "analysis.outcome.v1.json"
        if outcome.is_file():
            load_outcome_document(outcome, expected_job_id=job.id, expected_stage=job.kind)
        metrics = result / "analysis.metrics.v1.json"
        if metrics.is_file():
            document = load_metrics_document(metrics)
            if document["jobId"] != job.id or document["stage"] != job.kind:
                raise SessionArchiveError("Metrics identity does not match its archived job")
        snapshot = result / "cct.snapshot.json"
        if snapshot.is_file():
            validate_cct_snapshot(json.loads(snapshot.read_text(encoding="utf-8")))
        sbfl = result / DEFAULT_SBFL_FILENAME
        if sbfl.is_file():
            load_sbfl_report(sbfl)

    def _validate_installed_evidence(self, root_job: JobRecord) -> None:
        if root_job.kind != "localization":
            return
        outcome_path = self.jobs.outcome_path(root_job.id)
        outcome = load_outcome_document(
            outcome_path, expected_job_id=root_job.id, expected_stage=root_job.kind
        )
        if outcome["conclusion"]["status"] != "available":
            return
        service = EvidenceService(self.jobs, root_job.id)
        index = service.index()
        for candidate in index["candidates"]:
            if candidate["failureIds"]:
                service.detail(candidate["id"], candidate["failureIds"][0])
