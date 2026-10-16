from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

# Automatically discover and register backend and engine paths
backend_dir = Path(__file__).resolve().parents[1]
engine_dir = Path(__file__).resolve().parents[3] / "engine"

for path_str in (str(backend_dir), str(engine_dir)):
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

# Ensure subprocesses (like uvicorn auto-reloader) inherit these paths
existing_pythonpath = os.environ.get("PYTHONPATH", "")
extra_paths = [p for p in (str(backend_dir), str(engine_dir)) if p not in existing_pythonpath.split(os.pathsep)]
if extra_paths:
    os.environ["PYTHONPATH"] = os.pathsep.join(extra_paths + ([existing_pythonpath] if existing_pythonpath else []))

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="Start the CSC Web Workbench")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        app_dir=str(backend_dir),
    )


if __name__ == "__main__":
    main()
