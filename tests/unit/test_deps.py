"""Principal and dependency resolution edge cases."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from glide.api.deps import get_auth_service, get_demo_session, get_principal


class MissingSessionStore:
    def get(self, session_id):
        raise KeyError(session_id)


class MissingSettingsStore:
    def get_settings(self, user_id):
        return None


class CookieReader:
    def read(self, request):
        return SimpleNamespace(user_id="google:subject", email="user@example.com")


def test_get_auth_service_returns_app_service() -> None:
    service = object()
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(auth_service=service))
    )

    assert get_auth_service(request) is service


def test_get_demo_session_maps_missing_session_to_404() -> None:
    with pytest.raises(HTTPException) as exc_info:
        get_demo_session("missing", MissingSessionStore())

    assert exc_info.value.status_code == 404


def test_get_principal_maps_unknown_sample_header_to_404() -> None:
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                auth_service=None,
                demo_store=None,
                state_store=None,
            )
        )
    )

    with pytest.raises(HTTPException) as exc_info:
        get_principal(request, "missing", MissingSessionStore())

    assert exc_info.value.status_code == 404


def test_get_principal_maps_missing_live_settings_to_404() -> None:
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                auth_service=SimpleNamespace(cookies=CookieReader()),
                demo_store=None,
                state_store=None,
            )
        )
    )

    with pytest.raises(HTTPException) as exc_info:
        get_principal(request, None, None, MissingSettingsStore())

    assert exc_info.value.status_code == 404
