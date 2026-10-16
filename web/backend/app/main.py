from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .analysis_metrics import AnalysisMetricsService
from .analysis_outcomes import AnalysisOutcomeService
from .analysis_sources import AnalysisSourceResolver
from .api import router
from .config import Settings
from .core_adapter import CoreAdapter
from .jobs import JobManager
from .session_archive import SessionArchiveService
from .version import TOOL_VERSION
from .workspaces import WorkspaceStore


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings.from_environment()
    resolved.ensure_directories()
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.jobs.recover_interrupted_jobs()
        yield

    app = FastAPI(title="CSC Web Workbench", version=TOOL_VERSION, lifespan=lifespan)
    app.state.settings = resolved
    app.state.workspaces = WorkspaceStore(resolved.runtime_root / "workspaces")
    app.state.jobs = JobManager(resolved.runtime_root / "jobs")
    app.state.sessions = SessionArchiveService(
        resolved.runtime_root, app.state.workspaces, app.state.jobs
    )
    resolver = AnalysisSourceResolver(app.state.jobs)
    app.state.adapter = CoreAdapter(
        resolved,
        app.state.workspaces,
        app.state.jobs,
        finalizers=[
            AnalysisMetricsService(app.state.jobs, resolver),
            AnalysisOutcomeService(app.state.jobs, resolver),
        ],
    )
    app.include_router(router)

    assets = resolved.frontend_root / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.middleware("http")
    async def add_no_cache_headers(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/assets/"):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(
            resolved.frontend_root / "index.html",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )

    return app


app = create_app()
