"""Verify the deployed sample pipeline over HTTPS with the browser closed.

Drives the public CloudFront endpoint through the same flow as
``scripts/run_sample.py`` but against the real stack: create session ->
first check (one block, one decision) -> move the middle appointment ->
recheck (two blocks, no decisions) -> idempotent repeat -> wait for one
scheduled dispatcher run. Exits non-zero on any failed assertion.
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
RUN_TIMEOUT_SECONDS = 120.0
SCHEDULE_TIMEOUT_SECONDS = 480.0

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


def post_json(path: str, headers: dict[str, str], payload: dict) -> dict:
    response = requests.post(
        f"{BASE_URL}{path}", headers=headers, json=payload, timeout=30
    )
    if response.status_code >= 400:
        fail(f"{path} returned {response.status_code}: {response.text[:300]}")
    return response.json()


def patch_json(path: str, headers: dict[str, str], payload: dict) -> dict:
    response = requests.patch(
        f"{BASE_URL}{path}", headers=headers, json=payload, timeout=30
    )
    if response.status_code >= 400:
        fail(f"{path} returned {response.status_code}: {response.text[:300]}")
    return response.json()


def get_json(path: str, headers: dict[str, str]) -> dict:
    response = requests.get(f"{BASE_URL}{path}", headers=headers, timeout=30)
    if response.status_code >= 400:
        fail(f"{path} returned {response.status_code}: {response.text[:300]}")
    return response.json()


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

    print("5. Wait for one scheduled dispatcher run (browser closed)")
    deadline = time.monotonic() + SCHEDULE_TIMEOUT_SECONDS
    scheduled = None
    while time.monotonic() < deadline:
        day_state = get_json("/api/day", headers)
        last_run = day_state.get("last_run")
        if last_run and last_run.get("trigger") == "schedule":
            if last_run.get("status") in TERMINAL_RUN_STATUSES:
                scheduled = last_run
                break
        time.sleep(POLL_INTERVAL_SECONDS)
    if scheduled is None:
        fail("no scheduled dispatcher run observed before timeout")
    print(f"  scheduled run observed: status={scheduled['status']}")
    if scheduled["status"] not in {"completed", "needs_input"}:
        fail(
            f"scheduled run finished as {scheduled['status']} "
            f"(code={scheduled.get('safe_failure_code')})"
        )
    day_state = get_json("/api/day", headers)
    if len(day_state["travel_blocks"]) != 2:
        fail(
            f"scheduled run left {len(day_state['travel_blocks'])} block(s), "
            "expected 2"
        )
    print("  ok: scheduled run preserved the two blocks")

    print("Deployed sample pipeline verified.")


if __name__ == "__main__":
    main()
