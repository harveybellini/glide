"""Live connection teardown: pause, clean up, revoke.

Disconnect first disables automation so the dispatcher stops creating jobs,
then best-effort removes the owned future travel events, then revokes and deletes
the stored credential. Any failed step is reported as an explicit warning
rather than a silent half-disconnect. Cleanup never deletes a calendar or an
ordinary appointment.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from pydantic import BaseModel

from glide.adapters.interfaces import StateStore
from glide.domain.scheduling import block_hash


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
    clock: Callable[[], datetime] = field(
        default=lambda: datetime.now(UTC),
        repr=False,
    )

    def disconnect(self, user_id: str) -> DisconnectResult:
        settings = self.state_store.get_settings(user_id)
        if settings is None:
            return DisconnectResult(status="not_connected")

        # Pause first: the dispatcher only enqueues enabled tenants, so this
        # stops new jobs before any cleanup begins.
        self.state_store.save_settings(
            settings.model_copy(
                update={"enabled": False, "revision": settings.revision + 1}
            )
        )
        warnings: list[str] = []

        now = self.clock()
        try:
            calendar = self.calendar_factory(settings)
            blocks = calendar.list_blocks(
                calendar_id="primary",
                window_start=now - timedelta(seconds=self.lookback_seconds),
                window_end=now + timedelta(seconds=self.window_seconds),
            )
            preserved_manual_edit = False
            for block in blocks:
                if block.user_id != user_id or block.start <= now:
                    continue
                expected_hash = block_hash(
                    start=block.start,
                    end=block.end,
                    source_revision=block.source_revision,
                    padding_minutes=block.padding_minutes,
                )
                if block.manual_override or block.last_applied_hash != expected_hash:
                    preserved_manual_edit = True
                    continue
                calendar.delete_block(
                    calendar_id="primary",
                    event_id=block.provider_event_id,
                    expected_etag=block.etag,
                )
            if preserved_manual_edit:
                warnings.append(
                    "Glide travel events you edited manually were kept in your "
                    "primary calendar."
                )
        except Exception:  # noqa: BLE001 - teardown must always proceed
            warnings.append(
                "Some Glide travel events could not be cleaned up. Ordinary "
                "appointments and the calendar itself were left untouched; "
                "review events titled 'Travel · Glide' in Google Calendar."
            )

        if settings.legacy_glide_calendar_id or settings.glide_calendar_id not in {
            "",
            "primary",
        }:
            warnings.append(
                "An older separate Glide Travel calendar was left unchanged. "
                "Review it manually before removing any events."
            )

        try:
            self.revoker.revoke(user_id)
        except Exception:  # noqa: BLE001 - report, never block the sign-out
            warnings.append(
                "Credential revocation failed; disconnect Glide in your "
                "Google Account's third-party apps page."
            )

        return DisconnectResult(status="disconnected", warnings=warnings)
