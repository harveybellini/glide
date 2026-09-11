"""Small local SQLite state adapter for development and tests.

The deployed application uses DynamoDB; this adapter exists so the same state
contracts can be exercised on a laptop without cloud resources. It stores JSON
payloads from the frozen domain models and does not invent schema fields.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

from pydantic import TypeAdapter

from glide.domain.models import (
    Decision,
    JourneyPlan,
    ManagedBlock,
    MutationReceipt,
    Run,
    SampleSnapshot,
    UserSettings,
    WorkflowResult,
)

ModelT = TypeVar("ModelT")


class SqliteStateStore:
    def __init__(self, path: str | Path = "glide-local.db") -> None:
        self._connection = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.RLock()
        self._create_schema()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _create_schema(self) -> None:
        with self._lock, self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    user_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS plans (
                    run_id TEXT NOT NULL,
                    journey_key TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY (run_id, journey_key)
                );
                CREATE TABLE IF NOT EXISTS decisions (
                    decision_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS receipts (
                    receipt_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS blocks (
                    user_id TEXT NOT NULL,
                    journey_key TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY (user_id, journey_key)
                );
                CREATE TABLE IF NOT EXISTS sample_snapshots (
                    user_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS sample_snapshots_session
                    ON sample_snapshots (session_id);
                """
            )

    def _json(self, value: ModelT) -> str:
        return json.dumps(value.model_dump(mode="json"), separators=(",", ":"))

    def _parse(self, payload: str, model: type[ModelT]) -> ModelT:
        return model.model_validate_json(payload)

    def save_settings(self, settings: UserSettings) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO settings (user_id, payload, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (settings.user_id, self._json(settings), datetime.now(UTC).isoformat()),
            )

    def get_settings(self, user_id: str) -> UserSettings | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload FROM settings WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return self._parse(row[0], UserSettings) if row else None

    def save_run(self, run: Run) -> None:
        with self._lock, self._connection:
            self._save_run_unlocked(run)

    def _save_run_unlocked(self, run: Run) -> None:
        self._connection.execute(
            """
            INSERT INTO runs (run_id, user_id, payload)
            VALUES (?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET payload = excluded.payload
            """,
            (run.id, run.user_id, self._json(run)),
        )

    def get_run(self, run_id: str) -> Run | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload FROM runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        return self._parse(row[0], Run) if row else None

    def save_plans(self, run_id: str, plans: list[JourneyPlan]) -> None:
        with self._lock, self._connection:
            self._save_plans_unlocked(run_id, plans)

    def _save_plans_unlocked(self, run_id: str, plans: list[JourneyPlan]) -> None:
        self._connection.executemany(
            """
            INSERT INTO plans (run_id, journey_key, payload)
            VALUES (?, ?, ?)
            ON CONFLICT(run_id, journey_key) DO UPDATE SET payload = excluded.payload
            """,
            [(run_id, plan.journey_key, self._json(plan)) for plan in plans],
        )

    def get_plans(self, run_id: str) -> list[JourneyPlan]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT payload FROM plans WHERE run_id = ? ORDER BY journey_key",
                (run_id,),
            ).fetchall()
        adapter = TypeAdapter(list[JourneyPlan])
        return adapter.validate_python([json.loads(row[0]) for row in rows])

    def save_decisions(self, decisions: list[Decision]) -> None:
        with self._lock, self._connection:
            self._save_decisions_unlocked(decisions)

    def _save_decisions_unlocked(self, decisions: list[Decision]) -> None:
        self._connection.executemany(
            """
            INSERT INTO decisions (decision_id, user_id, payload)
            VALUES (?, ?, ?)
            ON CONFLICT(decision_id) DO UPDATE SET payload = excluded.payload
            """,
            [
                (decision.id, decision.user_id, self._json(decision))
                for decision in decisions
            ],
        )

    def get_decisions(self, user_id: str) -> list[Decision]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT payload FROM decisions WHERE user_id = ? ORDER BY decision_id",
                (user_id,),
            ).fetchall()
        adapter = TypeAdapter(list[Decision])
        return adapter.validate_python([json.loads(row[0]) for row in rows])

    def save_receipts(
        self,
        receipts: list[MutationReceipt],
        user_id: str = "",
    ) -> None:
        with self._lock, self._connection:
            self._save_receipts_unlocked(receipts, user_id)

    def _save_receipts_unlocked(
        self,
        receipts: list[MutationReceipt],
        user_id: str = "",
    ) -> None:
        self._connection.executemany(
            """
            INSERT INTO receipts (receipt_id, run_id, user_id, payload)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(receipt_id) DO UPDATE SET payload = excluded.payload
            """,
            [
                (receipt.id, receipt.run_id, user_id, self._json(receipt))
                for receipt in receipts
            ],
        )

    def get_receipts(self, run_id: str | None = None) -> list[MutationReceipt]:
        query = "SELECT payload FROM receipts"
        params: tuple[str, ...] = ()
        if run_id is not None:
            query += " WHERE run_id = ?"
            params = (run_id,)
        query += " ORDER BY receipt_id"
        with self._lock:
            rows = self._connection.execute(query, params).fetchall()
        adapter = TypeAdapter(list[MutationReceipt])
        return adapter.validate_python([json.loads(row[0]) for row in rows])

    def save_blocks(self, user_id: str, blocks: list[ManagedBlock]) -> None:
        with self._lock, self._connection:
            self._save_blocks_unlocked(user_id, blocks)

    def _save_blocks_unlocked(self, user_id: str, blocks: list[ManagedBlock]) -> None:
        self._connection.execute(
            "DELETE FROM blocks WHERE user_id = ?",
            (user_id,),
        )
        self._connection.executemany(
            """
            INSERT INTO blocks (user_id, journey_key, payload)
            VALUES (?, ?, ?)
            """,
            [(user_id, block.journey_key, self._json(block)) for block in blocks],
        )

    def get_blocks(self, user_id: str) -> list[ManagedBlock]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT payload FROM blocks WHERE user_id = ? ORDER BY journey_key",
                (user_id,),
            ).fetchall()
        adapter = TypeAdapter(list[ManagedBlock])
        return adapter.validate_python([json.loads(row[0]) for row in rows])

    def save_sample_snapshot(self, snapshot: SampleSnapshot) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO sample_snapshots (user_id, session_id, payload)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    session_id = excluded.session_id,
                    payload = excluded.payload
                """,
                (snapshot.user_id, snapshot.session_id, self._json(snapshot)),
            )

    def get_sample_snapshot(self, user_id: str) -> SampleSnapshot | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload FROM sample_snapshots WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        snapshot = self._parse(row[0], SampleSnapshot) if row else None
        return self._active_snapshot(snapshot)

    def get_sample_snapshot_by_session(self, session_id: str) -> SampleSnapshot | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload FROM sample_snapshots WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        snapshot = self._parse(row[0], SampleSnapshot) if row else None
        return self._active_snapshot(snapshot)

    @staticmethod
    def _active_snapshot(snapshot: SampleSnapshot | None) -> SampleSnapshot | None:
        if snapshot is None or snapshot.expires_at <= datetime.now(UTC):
            return None
        return snapshot

    def get_latest_run(self, user_id: str) -> Run | None:
        with self._lock:
            rows = self._connection.execute(
                "SELECT payload FROM runs WHERE user_id = ?",
                (user_id,),
            ).fetchall()
        runs = [self._parse(row[0], Run) for row in rows]
        return max(runs, key=lambda run: run.started_at, default=None)

    def get_user_receipts(self, user_id: str) -> list[MutationReceipt]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT payload FROM receipts WHERE user_id = ? ORDER BY receipt_id",
                (user_id,),
            ).fetchall()
        adapter = TypeAdapter(list[MutationReceipt])
        return adapter.validate_python([json.loads(row[0]) for row in rows])

    def save_result(self, result: WorkflowResult) -> None:
        """Persist a complete result in one transaction.

        The run status and its plans, decisions, receipts, and blocks commit
        together so a poller can never observe a terminal run with partial
        child records.
        """

        with self._lock, self._connection:
            self._save_run_unlocked(result.run)
            self._save_plans_unlocked(result.run.id, list(result.plans))
            self._save_decisions_unlocked(list(result.decisions))
            self._save_receipts_unlocked(list(result.receipts), user_id=result.run.user_id)
            self._save_blocks_unlocked(result.run.user_id, list(result.travel_blocks))

    def clear_user(self, user_id: str) -> None:
        """Delete every persisted record for one sample tenant."""

        with self._lock, self._connection:
            rows = self._connection.execute(
                "SELECT run_id FROM runs WHERE user_id = ?",
                (user_id,),
            ).fetchall()
            self._connection.executemany(
                "DELETE FROM plans WHERE run_id = ?",
                [(row[0],) for row in rows],
            )
            self._connection.execute(
                "DELETE FROM receipts WHERE user_id = ?",
                (user_id,),
            )
            self._connection.execute(
                "DELETE FROM decisions WHERE user_id = ?",
                (user_id,),
            )
            self._connection.execute(
                "DELETE FROM blocks WHERE user_id = ?",
                (user_id,),
            )
            self._connection.execute(
                "DELETE FROM runs WHERE user_id = ?",
                (user_id,),
            )
            self._connection.execute(
                "DELETE FROM sample_snapshots WHERE user_id = ?",
                (user_id,),
            )
