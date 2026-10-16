from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask

from csc_engine.metrics import MetricsContractError, load_metrics_document
from csc_engine.outcomes import OutcomeContractError

from .analysis_outcomes import load_job_outcome, upstream_provenance
from .evidence import EvidenceService, EvidenceUnavailable

from .models import (
    CSCJobRequest,
    JobStatus,
    LocalizationJobRequest,
    TBFVJobRequest,
)
from .outcome_policy import next_stages
from .session_archive import ARCHIVE_SUFFIX, SessionArchiveError
from .version import version_info


router = APIRouter(prefix="/api")
MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_SESSION_UPLOAD_BYTES = 256 * 1024 * 1024


def _state(request: Request):
    return request.app.state


@router.get("/health")
def health(request: Request) -> dict:
    settings = _state(request).settings
    return {
        "status": "ok" if not settings.validate() else "degraded",
        "core_root": str(settings.core_root),
        "runtime_root": str(settings.runtime_root),
        "errors": settings.validate(),
        "tools": {
            name: shutil.which(name)
            for name in ("java", "javac", "dot")
        },
        "bridge_built": (
            settings.core_root
            / "java_bridge"
            / "target"
            / "csc-bridge-0.1.0-jar-with-dependencies.jar"
        ).is_file(),
        "version": version_info(),
    }


@router.get("/version")
def get_version() -> dict[str, object]:
    return version_info()


@router.get("/workspaces")
def list_workspaces(request: Request):
    return _state(request).workspaces.list()


@router.get("/workspaces/{workspace_id}/source")
def get_workspace_source(request: Request, workspace_id: str):
    try:
        return _state(request).workspaces.get_source(workspace_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Workspace not found") from exc


@router.post("/workspaces", status_code=201)
async def create_workspace(
    request: Request,
    name: str = Form(""),
    java_file: UploadFile | None = File(None),
    fsf_file: UploadFile | None = File(None),
    java_source: str = Form(""),
    fsf_source: str = Form(""),
):
    has_java_file = java_file is not None and bool(java_file.filename)
    has_java_text = bool(java_source.strip())
    has_fsf_file = fsf_file is not None and bool(fsf_file.filename)
    has_fsf_text = bool(fsf_source.strip())
    if has_java_file == has_java_text:
        raise HTTPException(
            status_code=400,
            detail="Provide exactly one Java input: a file or pasted source",
        )
    if has_fsf_file and has_fsf_text:
        raise HTTPException(
            status_code=400,
            detail="Provide at most one FSF input: a file or pasted source",
        )

    try:
        if has_java_text:
            _check_text_size(java_source, "Java source")
            if has_fsf_text:
                _check_text_size(fsf_source, "FSF source")
            return _state(request).workspaces.create_from_text(
                name=name,
                java_source=java_source,
                fsf_source=fsf_source if has_fsf_text else None,
            )

        java_content = await _read_upload(java_file)
        fsf_content = await _read_upload(fsf_file) if has_fsf_file else None
        return _state(request).workspaces.create(
            name=name,
            java_filename=java_file.filename or "Target.java",
            java_source=java_content,
            fsf_filename=fsf_file.filename if has_fsf_file else None,
            fsf_source=fsf_content,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/jobs")
def list_jobs(request: Request):
    return _state(request).jobs.list()


@router.get("/history")
def list_history(request: Request):
    """List persisted runs without selecting one as the active analysis."""
    workspaces = {
        workspace.id: workspace
        for workspace in _state(request).workspaces.list()
    }
    return [
        {
            "id": job.id,
            "stage": job.kind,
            "status": job.status,
            "created_at": job.created_at,
            "finished_at": job.finished_at,
            "duration_available": bool(job.started_at and job.finished_at),
            "workspace_id": job.workspace_id,
            "program": workspaces.get(job.workspace_id),
            "config": job.config,
            "restorable": job.workspace_id in workspaces,
            "exportable": (
                job.status is JobStatus.succeeded
                and job.workspace_id in workspaces
            ),
            "archived": job.archive is not None,
            "session_id": job.archive.session_id if job.archive else None,
        }
        for job in _state(request).jobs.list()
    ]


@router.post("/history/{job_id}/restore")
def restore_history(request: Request, job_id: str):
    """Resolve a persisted run into a frontend-loadable analysis context."""
    try:
        job = _state(request).jobs.get(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Historical run not found") from exc
    try:
        workspace = _state(request).workspaces.get(job.workspace_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=409,
            detail="The program input for this historical run is unavailable",
        ) from exc
    return {
        "job": job,
        "program": workspace,
        "viewMode": "archived" if job.archive else "live",
    }


@router.get("/history/{job_id}/export")
def export_history(request: Request, job_id: str):
    handle = tempfile.NamedTemporaryFile(
        prefix="csc-session-",
        suffix=ARCHIVE_SUFFIX,
        dir=_state(request).settings.runtime_root,
        delete=False,
    )
    destination = Path(handle.name)
    handle.close()
    try:
        path, manifest = _state(request).sessions.export(job_id, destination)
        class_name = manifest["workspace"]["class_name"]
        stage = next(
            record["kind"] for record in manifest["jobs"]
            if record["id"] == manifest["rootJobId"]
        )
        filename = f"{class_name}-{stage}-{job_id[:8]}{ARCHIVE_SUFFIX}"
        return FileResponse(
            path,
            media_type="application/zip",
            filename=filename,
            background=BackgroundTask(path.unlink, missing_ok=True),
            headers={
                "X-CSC-Session-ID": manifest["sessionId"],
                "Cache-Control": "no-store",
            },
        )
    except SessionArchiveError as exc:
        destination.unlink(missing_ok=True)
        status = 404 if "not found" in str(exc).lower() else 409
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@router.post("/history/import", status_code=201)
async def import_history_archive(
    request: Request,
    session_file: UploadFile = File(...),
):
    if not (session_file.filename or "").endswith(ARCHIVE_SUFFIX):
        raise HTTPException(
            status_code=400,
            detail=f"Expected a {ARCHIVE_SUFFIX} file",
        )
    handle = tempfile.NamedTemporaryFile(
        prefix="csc-session-upload-",
        suffix=ARCHIVE_SUFFIX,
        dir=_state(request).settings.runtime_root,
        delete=False,
    )
    temporary = Path(handle.name)
    size = 0
    try:
        while chunk := await session_file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_SESSION_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail="Session archive exceeds 256 MiB compressed limit",
                )
            handle.write(chunk)
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        try:
            return _state(request).sessions.import_archive(temporary)
        except SessionArchiveError as exc:
            status = 409 if "already exist" in str(exc).lower() else 400
            raise HTTPException(status_code=status, detail=str(exc)) from exc
    finally:
        if not handle.closed:
            handle.close()
        temporary.unlink(missing_ok=True)


@router.get("/jobs/{job_id}")
def get_job(request: Request, job_id: str):
    try:
        return _state(request).jobs.get(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc


@router.post("/jobs/csc", status_code=202)
def start_csc(request: Request, payload: CSCJobRequest):
    return _start(request, lambda: _state(request).adapter.start_csc(payload))


@router.post("/jobs/tbfv", status_code=202)
def start_tbfv(request: Request, payload: TBFVJobRequest):
    return _start(request, lambda: _state(request).adapter.start_tbfv(payload))


@router.post("/jobs/localization", status_code=202)
def start_localization(request: Request, payload: LocalizationJobRequest):
    return _start(
        request,
        lambda: _state(request).adapter.start_localization(payload),
    )


@router.post("/jobs/{job_id}/cancel")
def cancel_job(request: Request, job_id: str):
    try:
        return _state(request).jobs.cancel(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc


@router.get("/jobs/{job_id}/artifacts")
def list_artifacts(request: Request, job_id: str):
    try:
        return _state(request).jobs.artifacts(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc


@router.get("/jobs/{job_id}/cct-snapshot", response_class=FileResponse)
def get_cct_snapshot(request: Request, job_id: str):
    try:
        path = _state(request).jobs.cct_snapshot_path(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="CCT snapshot not found") from exc
    return FileResponse(path, media_type="application/json")


@router.get("/jobs/{job_id}/metrics")
def get_analysis_metrics(request: Request, job_id: str):
    jobs = _state(request).jobs
    try:
        job = jobs.get(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc
    try:
        if job.status is not JobStatus.succeeded or any(w.component == "metrics" for w in job.finalization_warnings):
            raise KeyError(job_id)
        path = jobs.metrics_path(job_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail="Analysis metrics are not available for this job",
        ) from exc
    try:
        return load_metrics_document(path)
    except MetricsContractError as exc:
        raise HTTPException(
            status_code=409,
            detail="Analysis metrics artifact is invalid",
        ) from exc


@router.get("/jobs/{job_id}/outcome")
def get_analysis_outcome(request: Request, job_id: str):
    jobs = _state(request).jobs
    try:
        job = jobs.get(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc
    if job.status in {JobStatus.pending, JobStatus.running}:
        raise HTTPException(
            status_code=404,
            detail=(
                "Analysis outcome is not available for this job "
                "(the job has not finished)"
            ),
        )
    if job.status is not JobStatus.succeeded:
        raise HTTPException(
            status_code=404,
            detail=(
                "Analysis outcome is not available for this job "
                "(the job did not succeed)"
            ),
        )
    try:
        document = load_job_outcome(jobs, job)
    except KeyError as exc:
        if any(warning.component == "outcome" for warning in job.finalization_warnings):
            detail = (
                "Analysis outcome is not available for this job "
                "(finalization failed)"
            )
        else:
            detail = (
                "Analysis outcome is not available for this job "
                "(not recorded by this version)"
            )
        raise HTTPException(status_code=404, detail=detail) from exc
    except OutcomeContractError as exc:
        raise HTTPException(
            status_code=409,
            detail="Analysis outcome artifact is invalid",
        ) from exc
    return {
        "outcome": document,
        "nextStages": next_stages(document["stage"], document),
        "upstream": upstream_provenance(jobs, job),
    }


def _read_evidence(request: Request, job_id: str, candidate_id=None, failure_id=None):
    try:
        _state(request).jobs.get(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc
    try:
        service = EvidenceService(_state(request).jobs, job_id)
        return service.index() if candidate_id is None else service.detail(candidate_id, failure_id)
    except (EvidenceUnavailable, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail="Recorded evidence is unavailable") from exc
    except (ValueError, TypeError, KeyError, OSError, AttributeError) as exc:
        raise HTTPException(status_code=409, detail="Recorded evidence is invalid or has broken links") from exc


@router.get("/jobs/{job_id}/evidence")
def get_evidence(request: Request, job_id: str):
    return _read_evidence(request, job_id)


@router.get("/jobs/{job_id}/evidence/{candidate_id}/{failure_id}")
def get_evidence_detail(request: Request, job_id: str, candidate_id: str, failure_id: str):
    return _read_evidence(request, job_id, candidate_id, failure_id)


@router.get("/jobs/{job_id}/files/{artifact_path:path}")
def get_artifact(request: Request, job_id: str, artifact_path: str):
    try:
        path = _state(request).jobs.artifact_path(job_id, artifact_path)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Artifact not found") from exc
    return FileResponse(path)


@router.get("/jobs/{job_id}/events")
def stream_job_events(request: Request, job_id: str):
    try:
        _state(request).jobs.get(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc
    return StreamingResponse(
        _event_stream(request, job_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


async def _event_stream(request: Request, job_id: str) -> AsyncIterator[str]:
    offset = 0
    last_status = None
    while True:
        job = _state(request).jobs.get(job_id)
        chunk, offset = _state(request).jobs.read_log(job_id, offset)
        payload = {
            "status": job.status,
            "log": chunk,
            "offset": offset,
            "return_code": job.return_code,
            "error": job.error,
        }
        if chunk or job.status != last_status:
            yield f"data: {json.dumps(payload)}\n\n"
            last_status = job.status
        if job.status.value in {"succeeded", "failed", "cancelled"}:
            break
        if await request.is_disconnected():
            break
        await asyncio.sleep(0.5)


def _start(request: Request, action):
    try:
        return action()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Resource not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


async def _read_upload(upload: UploadFile | None) -> bytes:
    if upload is None:
        return b""
    content = await upload.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds 2 MiB limit")
    return content


def _check_text_size(value: str, label: str) -> None:
    if len(value.encode("utf-8")) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"{label} exceeds 2 MiB limit")
