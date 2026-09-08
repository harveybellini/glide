from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from glide.adapters.fixtures import canonical_settings
from glide.adapters.sqlite import SqliteStateStore
from glide.domain.models import ManagedBlock, UserSettings
from glide.live.disconnect import DisconnectService

from tests.unit.test_live_workflow import FakeCalendarAdapter


class FakeRevoker:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[str] = []

    def revoke(self, user_id: str) -> None:
        self.calls.append(user_id)
        if self.fail:
            raise RuntimeError("revocation failed")


def _block(journey_key: str, event_id: str) -> ManagedBlock:
    now = datetime.now(UTC)
    return ManagedBlock(
        journey_key=journey_key,
        user_id="google:subject",
        origin_occurrence_id="occ_a",
        destination_occurrence_id="occ_b",
        provider_event_id=event_id,
        start=now + timedelta(hours=1),
        end=now + timedelta(hours=1, minutes=30),
        last_applied_hash="hash",
        etag="etag",
        source_revision="revision",
        policy_revision=1,
    )


def _settings() -> UserSettings:
    return UserSettings.model_validate(
        canonical_settings(user_id="google:subject")
    )


def test_disconnect_pauses_cleans_up_and_revokes(tmp_path) -> None:
    store = SqliteStateStore(tmp_path / "glide.db")
    store.save_settings(_settings())
    adapter = FakeCalendarAdapter(
        blocks=[_block("jk-a", "travel-a"), _block("jk-b", "travel-b")]
    )
    revoker = FakeRevoker()
    service = DisconnectService(
        state_store=store,
        revoker=revoker,
        calendar_factory=lambda settings: adapter,
    )

    result = service.disconnect("google:subject")

    assert result.status == "disconnected"
    assert result.warnings == []
    assert store.get_settings("google:subject").enabled is False
    assert len(adapter.blocks) == 0
    assert revoker.calls == ["google:subject"]
    store.close()


def test_disconnect_reports_failed_cleanup_and_still_revokes(tmp_path) -> None:
    store = SqliteStateStore(tmp_path / "glide.db")
    store.save_settings(_settings())
    adapter = FakeCalendarAdapter(blocks=[_block("jk-a", "travel-a")])
    adapter.conflict_event_ids.add("travel-a")
    revoker = FakeRevoker()
    service = DisconnectService(
        state_store=store,
        revoker=revoker,
        calendar_factory=lambda settings: adapter,
    )

    result = service.disconnect("google:subject")

    assert result.status == "disconnected"
    assert any("remove the" in warning for warning in result.warnings)
    assert revoker.calls == ["google:subject"]
    store.close()


def test_disconnect_reports_failed_revocation(tmp_path) -> None:
    store = SqliteStateStore(tmp_path / "glide.db")
    store.save_settings(_settings())
    adapter = FakeCalendarAdapter(blocks=[_block("jk-a", "travel-a")])
    service = DisconnectService(
        state_store=store,
        revoker=FakeRevoker(fail=True),
        calendar_factory=lambda settings: adapter,
    )

    result = service.disconnect("google:subject")

    assert result.status == "disconnected"
    assert any("revocation failed" in warning for warning in result.warnings)
    assert len(adapter.blocks) == 0
    store.close()


def test_disconnect_without_settings_is_a_noop(tmp_path) -> None:
    revoker = FakeRevoker()
    service = DisconnectService(
        state_store=SqliteStateStore(tmp_path / "glide.db"),
        revoker=revoker,
        calendar_factory=lambda settings: pytest.fail("unexpected calendar use"),
    )

    result = service.disconnect("missing-user")

    assert result.status == "not_connected"
    assert revoker.calls == []
