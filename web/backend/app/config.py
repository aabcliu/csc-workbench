from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    project_root: Path
    core_root: Path
    runtime_root: Path
    frontend_root: Path
    python_executable: str

    @classmethod
    def from_environment(cls) -> "Settings":
        project_root = Path(__file__).resolve().parents[2]
        distribution_root = project_root.parent
        return cls(
            project_root=project_root,
            core_root=Path(
                os.environ.get(
                    "CSC_ENGINE_ROOT",
                    distribution_root / "engine",
                )
            ).resolve(),
            runtime_root=Path(
                os.environ.get(
                    "CSC_WEB_DATA_ROOT",
                    project_root / "runtime",
                )
            ).resolve(),
            frontend_root=Path(
                os.environ.get(
                    "CSC_FRONTEND_ROOT",
                    project_root / "frontend",
                )
            ).resolve(),
            python_executable=os.environ.get(
                "CSC_PYTHON_EXECUTABLE",
                sys.executable,
            ),
        )

    def ensure_directories(self) -> None:
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        (self.runtime_root / "workspaces").mkdir(exist_ok=True)
        (self.runtime_root / "jobs").mkdir(exist_ok=True)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not (self.core_root / "csc_tool.py").is_file():
            errors.append(f"CSC core not found: {self.core_root}")
        if not self.frontend_root.is_dir():
            errors.append(f"Frontend not found: {self.frontend_root}")
        return errors
