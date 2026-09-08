"""Test environment defaults, set before any application module is imported."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_TEMP_DIR = tempfile.TemporaryDirectory(prefix="glide-tests-")
os.environ.setdefault("GLIDE_LOCAL_DB", str(Path(_TEMP_DIR.name) / "glide-test.db"))
os.environ.setdefault("GLIDE_WORKER_POLL_INTERVAL", "0.05")
os.environ.setdefault("GLIDE_SCHEDULE_INTERVAL", "0")


def pytest_sessionfinish(session, exitstatus) -> None:  # noqa: ARG001
    app_module = sys.modules.get("glide.api.app")
    if app_module is not None:
        app_module.app.state.state_store.close()
    _TEMP_DIR.cleanup()
