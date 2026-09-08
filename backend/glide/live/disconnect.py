"""Live connection teardown: pause, clean up, revoke.

Disconnect first disables automation so the dispatcher stops creating jobs,
then best-effort removes the owned travel blocks, then revokes and deletes
the stored credential. Any failed step is reported as an explicit warning
rather than a silent half-disconnect; a failed cleanup points the user at the
separate calendar they can remove manually.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from pydantic import BaseModel

from glide.adapters.interfaces import StateStore


class CredentialRevoker(Protocol):
    def revoke(self, user_id: str) -> None: ...


class DisconnectResult(BaseModel):
    status: str
    warnings: list[str] = field(default_factory=list)


@dataclass
class DisconnectService:
    state_store: StateStore
    revoker: CredentialRevoker
    calendar_factory: Any
    lookback_seconds: int = 3600
    window_seconds: int = 48 * 3600

    def disconnect(self, user_id: str) -> DisconnectResult:
        settings = self.state_store.get_settings(user_id)
        if settings is None:
            return DisconnectResult(status="not_connected")

        # Pause first: the dispatcher only enqueues enabled tenants, so this
        # stops new jobs before any cleanup begins.
        self.state_store.save_settings(settings.model_copy(update={"enabled": False}))
        warnings: list[str] = []

        now = datetime.now(UTC)
        try:
            calendar = self.calendar_factory(settings)
            blocks = calendar.list_blocks(
                calendar_id=settings.glide_calendar_id,
                window_start=now - timedelta(seconds=self.lookback_seconds),
                window_end=now + timedelta(seconds=self.window_seconds),
            )
            for block in blocks:
                calendar.delete_block(
                    calendar_id=settings.glide_calendar_id,
                    event_id=block.provider_event_id,
                    expected_etag=block.etag,
                )
        except Exception:  # noqa: BLE001 - teardown must always proceed
            warnings.append(
                "Travel calendar cleanup failed; you can remove the "
                '"Glide Travel" calendar manually in Google Calendar.'
            )

        try:
            self.revoker.revoke(user_id)
        except Exception:  # noqa: BLE001 - report, never block the sign-out
            warnings.append(
                "Credential revocation failed; disconnect Glide in your "
                "Google Account's third-party apps page."
            )

        return DisconnectResult(status="disconnected", warnings=warnings)
