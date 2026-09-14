"""Verify the deployed sample pipeline over HTTPS with the browser closed.

Drives the public CloudFront endpoint through the same flow as
``scripts/run_sample.py`` but against the real stack: create session ->
first check (one block, one decision) -> move the middle appointment ->
recheck (two blocks, no decisions) -> idempotent repeat -> confirm the sample
is watching and a scheduled dispatcher run completes with no browser action.
Exits non-zero on any failed assertion.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

BASE_URL = os.environ.get(
    "GLIDE_BASE_URL", "https://d3tvxy281s2u11.cloudfront.net"
).rstrip("/")
TIME_ZONE = ZoneInfo("Europe/London")
POLL_INTERVAL_SECONDS = 2.0
# The deployed worker gives the agent a 200-second application deadline, so a
# slow-but-successful run can legitimately take longer than two minutes to
# reach a terminal status. Keep this well above that deadline.
RUN_TIMEOUT_SECONDS = 300.0
# The dispatcher is the deployed five-minute EventBridge tick, so the first
# sample check can legitimately take up to one tick plus queue and worker
# time. Keep a little headroom above two ticks before failing.
SCHEDULE_TIMEOUT_SECONDS = 900.0
REQUEST_ATTEMPTS = 6
RETRY_STATUSES = {429, 500, 502, 503, 504}

TERMINAL_RUN_STATUSES = {
    "completed",
    "needs_input",
    "failed",
    "superseded",
    "paused",
}


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    sys.exit(1)


def session_headers(session_id: str) -> dict[str, str]:
    return {"X-Glide-Session": session_id}


def request(method: str, path: str, headers: dict[str, str], payload: dict | None = None):
    """Call the deployed API, tolerating brief origin flaps.

    CloudFront has been observed returning 503 for every route for tens of
    seconds at a time, so a single 503 is not evidence about the deployment.
    """
    last_status = None
    for attempt in range(REQUEST_ATTEMPTS):
        try:
            response = requests.request(
                method,
                f"{BASE_URL}{path}",
                headers=headers,
                json=payload,
                timeout=30,
            )
        except requests.RequestException as exc:
            print(f"  retry {attempt + 1}/{REQUEST_ATTEMPTS} {method} {path}: {exc}")
            time.sleep(5.0)
            continue
        if response.status_code in RETRY_STATUSES:
            last_status = response.status_code
            print(
                f"  retry {attempt + 1}/{REQUEST_ATTEMPTS} {method} {path}: "
                f"HTTP {response.status_code}"
            )
            time.sleep(5.0)
            continue
        if response.status_code >= 400:
            fail(f"{path} returned {response.status_code}: {response.text[:300]}")
        return response
    fail(f"{path} kept failing after {REQUEST_ATTEMPTS} attempts (last {last_status})")


def post_json(path: str, headers: dict[str, str], payload: dict) -> dict:
    return request("POST", path, headers, payload).json()


def patch_json(path: str, headers: dict[str, str], payload: dict) -> dict:
    return request("PATCH", path, headers, payload).json()


def get_json(path: str, headers: dict[str, str]) -> dict:
    return request("GET", path, headers).json()


def queue_and_await_run(headers: dict[str, str]) -> dict:
    queued = post_json("/api/runs", headers, {"trigger": "sample"})
    run_id = queued["run_id"]
    print(f"  queued run {run_id}")
    deadline = time.monotonic() + RUN_TIMEOUT_SECONDS
    while True:
        result = get_json(f"/api/runs/{run_id}", headers)
        status = result["run"]["status"]
        if status in TERMINAL_RUN_STATUSES:
            print(f"  run finished: status={status}")
            return result
        if time.monotonic() > deadline:
            fail(f"run {run_id} did not reach a terminal status in time")
        time.sleep(POLL_INTERVAL_SECONDS)


def expect_blocks_decision(result: dict, blocks: int, decisions: int) -> None:
    actual_blocks = len(result["travel_blocks"])
    open_decisions = [
        d for d in result["decisions"] if d["status"] == "open"
    ]
    actual_decisions = len(open_decisions)
    if actual_blocks != blocks or actual_decisions != decisions:
        fail(
            f"expected {blocks} block(s) and {decisions} open decision(s), "
            f"got {actual_blocks} and {actual_decisions}"
        )
    print(
        f"  ok: {actual_blocks} block(s), {actual_decisions} open decision(s), "
        f"{len(result['receipts'])} receipt(s)"
    )


def main() -> None:
    print(f"Base URL: {BASE_URL}")

    print("1. Create a fresh deployed sample session")
    created = post_json("/api/demo/session", {}, {})
    session_id = created["session"]["session_id"]
    sample_date = created["sample_date"]
    headers = session_headers(session_id)
    print(f"  session={session_id} date={sample_date}")

    print("2. First check: one block plus one shortfall decision")
    result = queue_and_await_run(headers)
    expect_blocks_decision(result, 1, 1)

    print("3. Move the middle appointment and recheck")
    day = datetime.strptime(sample_date, "%Y-%m-%d").date()
    new_start = (
        datetime(day.year, day.month, day.day, 10, 45, tzinfo=TIME_ZONE)
        .astimezone()
        .isoformat()
    )
    new_end = (
        datetime(day.year, day.month, day.day, 11, 15, tzinfo=TIME_ZONE)
        .astimezone()
        .isoformat()
    )
    patch_json(
        "/api/demo/events/occ_b",
        headers,
        {"start": new_start, "end": new_end},
    )
    result = queue_and_await_run(headers)
    expect_blocks_decision(result, 2, 0)

    print("4. Repeat check: idempotent receipts")
    result = queue_and_await_run(headers)
    expect_blocks_decision(result, 2, 0)
    outcomes = {receipt["outcome"] for receipt in result["receipts"]}
    if outcomes and outcomes != {"unchanged"}:
        fail(f"repeat produced non-idempotent receipts: {sorted(outcomes)}")
    print(f"  ok: all {len(result['receipts'])} receipt(s) unchanged")

    # A sample watches its fictional day until its snapshot expires. The
    # dispatcher ticks every five minutes and enforces a 15-minute sample
    # floor, so a scheduled run should appear well inside this window.
    print("5. Wait for a scheduled background run with no browser open")
    started = time.monotonic()
    deadline = started + SCHEDULE_TIMEOUT_SECONDS
    scheduled = False
    while time.monotonic() < deadline:
        state = get_json("/api/day", headers)
        automation = state.get("automation") or {}
        if not automation.get("watching"):
            fail("the deployed sample is not watching after creation")
        last_run = state.get("last_run")
        if last_run and last_run.get("trigger") == "schedule":
            scheduled = True
            break
        time.sleep(POLL_INTERVAL_SECONDS)
    if not scheduled:
        fail("no scheduled sample run appeared within the dispatcher window")
    print(
        "  ok: the agent checked on its own after "
        f"{time.monotonic() - started:.0f}s (no browser action)"
    )

    day_state = get_json("/api/day", headers)
    if len(day_state["travel_blocks"]) != 2:
        fail(
            f"the sample now has {len(day_state['travel_blocks'])} block(s), "
            "expected 2"
        )
    print("  ok: both sample blocks are still present")

    print("Deployed sample pipeline verified.")


if __name__ == "__main__":
    main()
