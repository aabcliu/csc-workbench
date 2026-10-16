"""Deterministic, atomic persistence for Analysis Outcome artifacts.

Documents are validated by the runtime contract before the write and again
after it, so a persisted artifact is never trusted on the strength of the
in-memory copy alone.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .contract import OutcomeContractError, validate_outcome_document


def write_outcome_document(path: str | Path, document: Any) -> Path:
    """Validate and atomically write a deterministic outcome document."""
    destination = Path(path)
    validated = validate_outcome_document(document)
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(
        validated,
        indent=2,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
    ) + "\n"
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        temporary_path.replace(destination)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    load_outcome_document(destination)
    return destination


def load_outcome_document(
    path: str | Path,
    *,
    expected_job_id: str | None = None,
    expected_stage: str | None = None,
) -> dict[str, Any]:
    """Validate a persisted document and, when supplied, its owning job/stage."""
    source = Path(path)
    try:
        decoded = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OutcomeContractError(
            f"unable to load outcome document {source}: {exc}"
        ) from exc
    document = validate_outcome_document(decoded)
    if expected_job_id is not None and document["jobId"] != expected_job_id:
        raise OutcomeContractError("outcome jobId does not match its owning job")
    if expected_stage is not None and document["stage"] != expected_stage:
        raise OutcomeContractError("outcome stage does not match its owning job")
    return document
