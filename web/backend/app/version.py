from __future__ import annotations

import os


TOOL_NAME = "CSC Workbench"
TOOL_VERSION = "0.1.0"
SESSION_FORMAT = "csc.session"
SESSION_VERSION = 1
BUILD_REVISION = os.environ.get("CSC_BUILD_REVISION") or None


def version_info() -> dict[str, object]:
    return {
        "name": TOOL_NAME,
        "version": TOOL_VERSION,
        "buildRevision": BUILD_REVISION,
        "contracts": {
            "session": SESSION_VERSION,
            "outcome": 1,
            "metrics": 1,
            "cctSnapshot": 1,
            "evidenceView": 1,
        },
    }
