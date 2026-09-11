"""Amazon SES decision notifications.

The transport is deliberately small: one transactional email per newly open
decision, sent with the SES v2 ``SendEmail`` API from a verified identity.
Whether a message should be sent at all, and how often, is domain policy in
``glide.domain.notifications``.

Calendar content is user data. Only the facts Glide calculated are rendered
(never raw event titles), every interpolated value is HTML-escaped, and a
failed send raises so the caller can leave the decision un-notified and retry
on the next scheduled run.
"""

from __future__ import annotations

from html import escape
from typing import Any
from urllib.parse import quote

from glide.domain.models import Decision, UserSettings

# Copy for each decision reason the planner can raise. ``summary`` may use the
# calculated facts; anything missing degrades to the generic sentence.
_REASONS: dict[str, str] = {
    "insufficient_time": (
        "There is not enough time between two appointments for the drive."
    ),
    "unknown_location": (
        "One of the appointments does not have a location Glide can navigate to."
    ),
    "unknown_start": (
        "Glide needs your starting point before it can reserve travel time."
    ),
    "manual_edit": (
        "You edited a travel block Glide manages, so it will not overwrite it."
    ),
    "manually_deleted": (
        "A travel block was deleted by hand. Glide will leave it alone until you decide."
    ),
    "hybrid_meeting": (
        "A meeting looks like it could be virtual or in person."
    ),
    "all_day": (
        "An all-day event overlaps a journey Glide is planning."
    ),
    "downstream_uncertain": (
        "An earlier journey needs a decision before Glide can plan the one after it."
    ),
}

_SUBJECTS: dict[str, str] = {
    "manual_edit": "You changed a travel block",
    "manually_deleted": "A Glide travel block was deleted",
    "hybrid_meeting": "Is this meeting in person?",
    "all_day": "An all-day event needs a decision",
    "unknown_location": "An appointment needs a location",
    "unknown_start": "Glide needs your starting point",
}


def decision_summary(decision: Decision) -> str:
    """One plain sentence describing why the user is being contacted."""

    summary = _REASONS.get(
        decision.reason,
        "Something about your travel time needs a decision.",
    )
    shortfall = decision.calculated_facts.get("shortfall_seconds")
    if isinstance(shortfall, int) and shortfall > 0:
        minutes = max(round(shortfall / 60), 1)
        return f"{summary} It is {minutes} minute{'s' if minutes != 1 else ''} short."
    return summary


def decision_subject(decision: Decision) -> str:
    """Short subject line; the shortfall is folded in when it is known."""

    base = _SUBJECTS.get(decision.reason, "Glide needs your decision")
    shortfall = decision.calculated_facts.get("shortfall_seconds")
    if (
        decision.reason == "insufficient_time"
        and isinstance(shortfall, int)
        and shortfall > 0
    ):
        minutes = max(round(shortfall / 60), 1)
        return f"Glide: {minutes} minute{'s' if minutes != 1 else ''} short"
    return base


class SesDecisionNotifier:
    """Send one "needs your decision" email through Amazon SES v2."""

    def __init__(
        self,
        *,
        client: Any,
        from_address: str,
        base_url: str | None = None,
        configuration_set: str | None = None,
    ) -> None:
        self._client = client
        self._from_address = from_address
        self._base_url = base_url.rstrip("/") if base_url else None
        self._configuration_set = configuration_set

    def send_decision_opened(
        self,
        *,
        settings: UserSettings,
        decision: Decision,
    ) -> None:
        recipient = settings.notification_email
        if not recipient:
            raise ValueError("notification_email is not set")
        link = (
            f"{self._base_url}/?decision={quote(decision.id, safe='')}"
            if self._base_url
            else None
        )
        request: dict[str, Any] = {
            "FromEmailAddress": self._from_address,
            "Destination": {"ToAddresses": [recipient]},
            "Content": {
                "Simple": {
                    "Subject": {"Data": decision_subject(decision), "Charset": "UTF-8"},
                    "Body": {
                        "Text": {
                            "Data": self._text_body(decision, link),
                            "Charset": "UTF-8",
                        },
                        "Html": {
                            "Data": self._html_body(decision, link),
                            "Charset": "UTF-8",
                        },
                    },
                }
            },
        }
        if self._configuration_set:
            request["ConfigurationSetName"] = self._configuration_set
        self._client.send_email(**request)

    @staticmethod
    def _text_body(decision: Decision, link: str | None) -> str:
        lines = [
            "Glide found something that needs you.",
            "",
            decision_summary(decision),
        ]
        if link:
            lines += ["", f"Review it: {link}"]
        lines += [
            "",
            "Glide stays quiet otherwise, and you can turn these messages off in Settings.",
        ]
        return "\n".join(lines)

    @staticmethod
    def _html_body(decision: Decision, link: str | None) -> str:
        summary = escape(decision_summary(decision))
        parts = [
            "<p>Glide found something that needs you.</p>",
            f"<p>{summary}</p>",
        ]
        if link:
            parts.append(
                "<p><a href=\"" + escape(link, quote=True) + "\">Review the decision</a></p>"
            )
        parts.append(
            "<p style=\"color:#666;font-size:13px\">"
            "Glide stays quiet otherwise, and you can turn these messages off in Settings."
            "</p>"
        )
        return "".join(parts)
