from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path

from csc_engine.java_exec import UNKNOWN_CLASS_NAME, parse_class_name, parse_top_level_md_def
from csc_engine.z3_helpers import build_input_intervals_from_range_bound

from .models import WorkspaceRecord


_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")
class WorkspaceStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def list(self) -> list[WorkspaceRecord]:
        records: list[WorkspaceRecord] = []
        for path in sorted(self.root.glob("*/workspace.json")):
            try:
                records.append(
                    WorkspaceRecord.model_validate_json(
                        path.read_text(encoding="utf-8")
                    )
                )
            except (OSError, ValueError):
                continue
        return sorted(records, key=lambda item: item.created_at, reverse=True)

    def get(self, workspace_id: str) -> WorkspaceRecord:
        path = self._workspace_dir(workspace_id) / "workspace.json"
        if not path.is_file():
            raise KeyError(workspace_id)
        return WorkspaceRecord.model_validate_json(
            path.read_text(encoding="utf-8")
        )

    def create(
        self,
        name: str,
        java_filename: str,
        java_source: bytes,
        fsf_filename: str | None = None,
        fsf_source: bytes | None = None,
    ) -> WorkspaceRecord:
        if not java_source.strip():
            raise ValueError("Java source must not be empty")
        safe_java = self._safe_filename(java_filename, ".java")
        workspace_id = uuid.uuid4().hex
        class_name = (
            self._declared_class_name(java_source) or Path(safe_java).stem
        )
        record = WorkspaceRecord(
            id=workspace_id,
            name=name.strip() or class_name,
            class_name=class_name,
            java_filename=safe_java,
            fsf_filename=(
                self._safe_filename(fsf_filename, ".txt")
                if fsf_filename and fsf_source is not None
                else None
            ),
        )
        workspace_dir = self._workspace_dir(workspace_id)
        input_dir = workspace_dir / "input"
        input_dir.mkdir(parents=True)
        (input_dir / safe_java).write_bytes(java_source)
        if record.fsf_filename and fsf_source is not None:
            (input_dir / record.fsf_filename).write_bytes(fsf_source)
        self._write_record(record)
        return record

    def create_from_text(
        self,
        name: str,
        java_source: str,
        fsf_source: str | None = None,
    ) -> WorkspaceRecord:
        """Create a normal file-backed workspace from pasted source text."""
        class_name = self.infer_java_class_name(java_source)
        has_fsf = fsf_source is not None and bool(fsf_source.strip())
        return self.create(
            name=name,
            java_filename=f"{class_name}.java",
            java_source=java_source.encode("utf-8"),
            fsf_filename=f"{class_name}_FSF.txt" if has_fsf else None,
            fsf_source=fsf_source.encode("utf-8") if has_fsf else None,
        )

    def copy_inputs_to(self, workspace_id: str, target: Path) -> WorkspaceRecord:
        record = self.get(workspace_id)
        source = self._workspace_dir(workspace_id) / "input"
        target.mkdir(parents=True, exist_ok=True)
        for path in source.iterdir():
            if path.is_file():
                shutil.copy2(path, target / path.name)
        return record

    def get_source(self, workspace_id: str) -> dict:
        record = self.get(workspace_id)
        input_dir = self._workspace_dir(workspace_id) / "input"
        java_path = input_dir / record.java_filename
        java_source = java_path.read_text(encoding="utf-8", errors="replace") if java_path.is_file() else ""
        fsf_source = None
        if record.fsf_filename:
            fsf_path = input_dir / record.fsf_filename
            if fsf_path.is_file():
                fsf_source = fsf_path.read_text(encoding="utf-8", errors="replace")
        var_types = parse_top_level_md_def(java_source)
        defaults = build_input_intervals_from_range_bound(var_types)
        return {
            "id": record.id,
            "name": record.name,
            "class_name": record.class_name,
            "java_filename": record.java_filename,
            "java_source": java_source,
            "fsf_filename": record.fsf_filename,
            "fsf_source": fsf_source,
            "input_parameters": [
                {"name": name, "type": type_name, "default_interval": defaults.get(name)}
                for name, type_name in var_types.items() if name != "return_value"
            ],
        }

    def input_path(self, workspace_id: str, filename: str) -> Path:
        record = self.get(workspace_id)
        if filename not in {record.java_filename, record.fsf_filename}:
            raise KeyError(filename)
        return self._workspace_dir(workspace_id) / "input" / filename

    def _workspace_dir(self, workspace_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}", workspace_id):
            raise KeyError(workspace_id)
        return self.root / workspace_id

    def _write_record(self, record: WorkspaceRecord) -> None:
        path = self._workspace_dir(record.id) / "workspace.json"
        path.write_text(
            json.dumps(record.model_dump(), indent=2) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _safe_filename(filename: str | None, suffix: str) -> str:
        raw = Path(filename or f"input{suffix}").name
        safe = _SAFE_NAME.sub("_", raw)
        if not safe.lower().endswith(suffix):
            raise ValueError(f"Expected a {suffix} file")
        return safe

    @staticmethod
    def _declared_class_name(java_source: bytes) -> str | None:
        """Class name the core tools will use for this source's artifact directory.

        Uploaded file names need not match the declared class, so the source is
        authoritative; the core tools key their output directories off the same
        parser, and a mismatch would strand every artifact outside the job's
        result directory.
        """
        parsed = parse_class_name(java_source.decode("utf-8", errors="replace"))
        return None if parsed == UNKNOWN_CLASS_NAME else parsed

    @staticmethod
    def infer_java_class_name(java_source: str) -> str:
        """Find the public class, falling back to the first class declaration."""
        if not java_source.strip():
            raise ValueError("Java source must not be empty")
        class_name = parse_class_name(java_source)
        if class_name == UNKNOWN_CLASS_NAME:
            raise ValueError("Could not find a Java class declaration")
        return class_name
