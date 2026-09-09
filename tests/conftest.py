"""Test environment defaults, set before any application module is imported."""

from __future__ import annotations

import os
import shutil
import sys
import uuid
from pathlib import Path

import pytest

_RUNTIME_ROOT = Path(__file__).resolve().parents[1] / "temp" / "pytest-runtime"
_RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
# The managed Windows environment does not grant pytest reliable access to
# directories created with Python's restrictive TemporaryDirectory ACLs. Use
# ordinary, uniquely named workspace directories for the application and the
# tests' path fixture.
_TEMP_DIR = _RUNTIME_ROOT / f"glide-tests-{uuid.uuid4().hex}"
_TEMP_DIR.mkdir()
os.environ.setdefault("GLIDE_LOCAL_DB", str(_TEMP_DIR / "glide-test.db"))
os.environ.setdefault("GLIDE_WORKER_POLL_INTERVAL", "0.05")
os.environ.setdefault("GLIDE_SCHEDULE_INTERVAL", "0")


@pytest.fixture
def tmp_path() -> Path:
    path = _TEMP_DIR / f"case-{uuid.uuid4().hex}"
    path.mkdir()
    yield path
    shutil.rmtree(path, ignore_errors=True)


def pytest_sessionfinish(session, exitstatus) -> None:  # noqa: ARG001
    app_module = sys.modules.get("glide.api.app")
    if app_module is not None and app_module.app is not None:
        app_module.app.state.state_store.close()
    shutil.rmtree(_TEMP_DIR, ignore_errors=True)
