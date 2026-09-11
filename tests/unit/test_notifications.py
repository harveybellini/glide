"""Decision notification policy and the SES transport."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from glide.adapters.notifications import (
    SesDecisionNotifier,
    decision_subject,
    decision_summary,
)
from glide.adapters.sqlite import SqliteStateStore
from glide.domain.models import (
    Decision,
    DecisionStatus,
    Run,
    RunStatus,
    UserSettings,
    WorkflowResult,
)
from glide.domain.notifications import (
    carry_notification_state,
    deliver_open_decisions,
)

USER_ID = "google:test-user"
NOW = datetime(2026, 9, 11, 18, 0, tzinfo=UTC)


def _decision(
    decision_id: str = "decision-1",
    *,
    status: DecisionStatus = DecisionStatus.OPEN,
    reason: str = "insufficient_time",
    facts: dict[str, int | str | bool] | None = None,
    notified_at: datetime | None = None,
) -> Decision:
    return Decision(
        id=decision_id,
        user_id=USER_ID,
        occurrence_id="occ-1",
        journey_key="journey-1",
        source_revision="rev-1",
        reason=reason,
        calculated_facts=(
            {"shortfall_seconds": 600} if facts is None else facts
        ),
        allowed_actions=("skip_journey",),
        status=status,
        notified_at=notified_at,
    )


def _result(
    decisions: list[Decision],
    *,
    status: RunStatus = RunStatus.NEEDS_INPUT,
) -> WorkflowResult:
    return WorkflowResult(
        run=Run(
            id="run-1",
            user_id=USER_ID,
            trigger="schedule",
            status=status,
            lease_revision=1,
            source_fingerprint="rev-1",
            started_at=NOW,
            ended_at=NOW,
        ),
        plans=(),
        decisions=tuple(decisions),
        travel_blocks=(),
        receipts=(),
        source_events=(),
    )


def _store(
    tmp_path: Any,
    *,
    email: str | None = "owner@example.com",
    notify: bool = True,
    name: str = "glide",
) -> SqliteStateStore:
    store = SqliteStateStore(str(tmp_path / f"{name}.db"))
    store.save_settings(
        UserSettings(
            user_id=USER_ID,
            time_zone="Europe/London",
            source_calendar_id="primary",
            padding_minutes=10,
            enabled=True,
            revision=1,
            notification_email=email,
            notify_on_decisions=notify,
        )
    )
    return store


class RecordingNotifier:
    def __init__(self, failures: int = 0) -> None:
        self.sent: list[str] = []
        self.remaining_failures = failures

    def send_decision_opened(
        self,
        *,
        settings: UserSettings,
        decision: Decision,
    ) -> None:
        del settings
        if self.remaining_failures:
            self.remaining_failures -= 1
            raise RuntimeError("ses is unavailable")
        self.sent.append(decision.id)


class FakeSesClient:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def send_email(self, **kwargs: Any) -> dict[str, str]:
        self.requests.append(kwargs)
        return {"MessageId": "ses-1"}


def test_open_decision_is_announced_once_and_stamped(tmp_path: Any) -> None:
    store = _store(tmp_path)
    notifier = RecordingNotifier()
    result = _result([_decision()])

    stamped = deliver_open_decisions(store, notifier, result, now=NOW)

    assert notifier.sent == ["decision-1"]
    assert [decision.notified_at for decision in stamped] == [NOW]
    assert store.get_decisions(USER_ID)[0].notified_at == NOW


def test_a_later_poll_does_not_announce_the_same_decision_again(
    tmp_path: Any,
) -> None:
    store = _store(tmp_path)
    notifier = RecordingNotifier()
    deliver_open_decisions(store, notifier, _result([_decision()]), now=NOW)

    # The next run rebuilds the decision from the calendar with no mark; the
    # mark must be carried back onto it before the result is persisted.
    fresh = _result([_decision()])
    carried = carry_notification_state(store, fresh)
    replayed = deliver_open_decisions(store, notifier, carried, now=NOW)

    assert notifier.sent == ["decision-1"]
    assert replayed == []
    assert carried.decisions[0].notified_at is not None


def test_clearing_the_mark_means_the_decision_is_notified_again(
    tmp_path: Any,
) -> None:
    store = _store(tmp_path)
    notifier = RecordingNotifier()
    deliver_open_decisions(store, notifier, _result([_decision()]), now=NOW)

    replayed = deliver_open_decisions(
        store,
        notifier,
        _result([_decision()]),
        now=NOW,
    )

    assert notifier.sent == ["decision-1", "decision-1"]
    assert len(replayed) == 1


def test_disabled_or_addressless_users_are_never_contacted(tmp_path: Any) -> None:
    disabled = _store(tmp_path, notify=False, name="disabled")
    silent = _store(tmp_path, email=None, name="silent")
    notifier = RecordingNotifier()

    assert deliver_open_decisions(disabled, notifier, _result([_decision()])) == []
    assert deliver_open_decisions(silent, notifier, _result([_decision()])) == []
    assert notifier.sent == []


def test_resolved_and_failed_runs_are_skipped(tmp_path: Any) -> None:
    store = _store(tmp_path)
    notifier = RecordingNotifier()
    resolved = _decision(status=DecisionStatus.RESOLVED)

    assert deliver_open_decisions(store, notifier, _result([resolved])) == []
    assert (
        deliver_open_decisions(
            store,
            notifier,
            _result([_decision()], status=RunStatus.FAILED),
        )
        == []
    )
    assert notifier.sent == []


def test_a_failed_send_is_retried_instead_of_being_marked(tmp_path: Any) -> None:
    store = _store(tmp_path)
    notifier = RecordingNotifier(failures=1)
    result = _result([_decision()])
    # The processor persists the completed result before it notifies, so a
    # transport failure leaves the decision stored but unmarked.
    store.save_run(result.run)
    store.save_decisions(list(result.decisions))

    assert deliver_open_decisions(store, notifier, result) == []
    assert store.get_decisions(USER_ID)[0].notified_at is None

    assert deliver_open_decisions(store, notifier, result) != []
    assert notifier.sent == ["decision-1"]


def test_ses_notifier_sends_one_transactional_email() -> None:
    client = FakeSesClient()
    notifier = SesDecisionNotifier(
        client=client,
        from_address="glide@example.com",
        base_url="https://glide.example.com/",
        configuration_set="glide-decisions",
    )

    notifier.send_decision_opened(
        settings=UserSettings(
            user_id=USER_ID,
            time_zone="Europe/London",
            source_calendar_id="primary",
            notification_email="owner@example.com",
        ),
        decision=_decision(),
    )

    request = client.requests[0]
    assert request["FromEmailAddress"] == "glide@example.com"
    assert request["Destination"] == {"ToAddresses": ["owner@example.com"]}
    assert request["ConfigurationSetName"] == "glide-decisions"
    simple = request["Content"]["Simple"]
    assert simple["Subject"]["Data"] == "Glide: 10 minutes short"
    assert "10 minutes short" in simple["Body"]["Text"]["Data"]
    link = "https://glide.example.com/?decision=decision-1"
    assert link in simple["Body"]["Text"]["Data"]


def test_email_copy_reports_unknown_reasons_without_breaking() -> None:
    decision = _decision(reason="something_new", facts={})

    assert decision_subject(decision) == "Glide needs your decision"
    assert decision_summary(decision) == (
        "Something about your travel time needs a decision."
    )


def test_html_body_escapes_the_decision_link() -> None:
    client = FakeSesClient()
    notifier = SesDecisionNotifier(
        client=client,
        from_address="glide@example.com",
        base_url="https://glide.example.com",
    )
    notifier.send_decision_opened(
        settings=UserSettings(
            user_id=USER_ID,
            time_zone="Europe/London",
            source_calendar_id="primary",
            notification_email="owner@example.com",
        ),
        decision=_decision('bad"><script>alert(1)</script>'),
    )

    html = client.requests[0]["Content"]["Simple"]["Body"]["Html"]["Data"]
    assert "<script>" not in html
    assert "%3Cscript%3E" in html
