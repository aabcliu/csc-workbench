"""Deterministic, atomic persistence for Analysis Metrics artifacts."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .contract import MetricsContractError, validate_metrics_document


DEFAULT_METRICS_FILENAME = "analysis.metrics.v1.json"


def write_metrics_document(path: str | Path, document: Any) -> Path:
    """Validate and atomically write a deterministic metrics document."""
    destination = Path(path)
    validated = validate_metrics_document(document)
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(
        validated,
        indent=2,
        ensure_ascii=False,
        allow_nan=False,
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
    return destination


def load_metrics_document(path: str | Path) -> dict[str, Any]:
    """Load and validate a persisted metrics document."""
    source = Path(path)
    try:
        decoded = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MetricsContractError(f"unable to load metrics document {source}: {exc}") from exc
    return validate_metrics_document(decoded)

