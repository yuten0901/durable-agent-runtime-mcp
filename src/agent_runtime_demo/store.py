from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from .models import RunStatus, StepRecord, StepSpec, StepStatus


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class DurableStore:
    """SQLite checkpoint store。schemaはD1移植時に分離検証できる単純SQLに限定する。"""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path)
        self._connection.row_factory = sqlite3.Row
        self._create_schema()

    def close(self) -> None:
        self._connection.close()

    def _create_schema(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                fixture_id TEXT NOT NULL,
                fixture_digest TEXT,
                fixture_snapshot_json TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS steps (
                run_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                agent TEXT NOT NULL,
                action TEXT NOT NULL,
                dependencies_json TEXT NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                result_json TEXT,
                error TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (run_id, step_id)
            );
            CREATE TABLE IF NOT EXISTS traces (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                event TEXT NOT NULL,
                agent TEXT,
                detail_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        self._ensure_run_binding_columns()
        self._connection.commit()

    def _ensure_run_binding_columns(self) -> None:
        columns = {
            str(row["name"])
            for row in self._connection.execute("PRAGMA table_info(runs)").fetchall()
        }
        if "fixture_digest" not in columns:
            self._connection.execute("ALTER TABLE runs ADD COLUMN fixture_digest TEXT")
        if "fixture_snapshot_json" not in columns:
            self._connection.execute("ALTER TABLE runs ADD COLUMN fixture_snapshot_json TEXT")

    def create_run(
        self,
        run_id: str,
        fixture_id: str,
        steps: Iterable[StepSpec],
        *,
        fixture_digest: str,
        fixture_snapshot_json: str,
    ) -> None:
        now = utc_now()
        with self._connection:
            existing = self._connection.execute(
                "SELECT fixture_id, fixture_digest, fixture_snapshot_json "
                "FROM runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if existing is None:
                self._connection.execute(
                    """
                    INSERT INTO runs
                    (run_id, fixture_id, fixture_digest, fixture_snapshot_json,
                     status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 'pending', ?, ?)
                    """,
                    (
                        run_id,
                        fixture_id,
                        fixture_digest,
                        fixture_snapshot_json,
                        now,
                        now,
                    ),
                )
            elif (
                existing["fixture_id"] != fixture_id
                or existing["fixture_digest"] != fixture_digest
                or existing["fixture_snapshot_json"] != fixture_snapshot_json
            ):
                raise ValueError("fixture_binding_mismatch")
            for step in steps:
                self._connection.execute(
                    """
                    INSERT OR IGNORE INTO steps
                    (run_id, step_id, agent, action, dependencies_json, status, updated_at)
                    VALUES (?, ?, ?, ?, ?, 'pending', ?)
                    """,
                    (
                        run_id,
                        step.step_id,
                        step.agent,
                        step.action,
                        json.dumps(step.dependencies),
                        now,
                    ),
                )

    def run_exists(self, run_id: str) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        return row is not None

    def fixture_id(self, run_id: str) -> str:
        row = self._connection.execute(
            "SELECT fixture_id FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return str(row["fixture_id"])

    def fixture_binding(self, run_id: str) -> tuple[str, str]:
        row = self._connection.execute(
            "SELECT fixture_digest, fixture_snapshot_json FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise KeyError(run_id)
        digest = row["fixture_digest"]
        snapshot = row["fixture_snapshot_json"]
        if (
            not isinstance(digest, str)
            or not digest
            or not isinstance(snapshot, str)
            or not snapshot
        ):
            raise ValueError("fixture_binding_missing")
        return digest, snapshot

    def run_status(self, run_id: str) -> RunStatus:
        row = self._connection.execute(
            "SELECT status FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return cast(RunStatus, row["status"])

    def set_run_status(self, run_id: str, status: RunStatus) -> None:
        with self._connection:
            self._connection.execute(
                "UPDATE runs SET status = ?, updated_at = ? WHERE run_id = ?",
                (status, utc_now(), run_id),
            )

    def steps(self, run_id: str) -> tuple[StepRecord, ...]:
        rows = self._connection.execute(
            "SELECT * FROM steps WHERE run_id = ? ORDER BY rowid", (run_id,)
        ).fetchall()
        return tuple(
            StepRecord(
                step_id=row["step_id"],
                agent=row["agent"],
                action=row["action"],
                status=cast(StepStatus, row["status"]),
                attempts=row["attempts"],
                result=json.loads(row["result_json"]) if row["result_json"] else None,
                error=row["error"],
            )
            for row in rows
        )

    def dependencies(self, run_id: str, step_id: str) -> tuple[str, ...]:
        row = self._connection.execute(
            "SELECT dependencies_json FROM steps WHERE run_id = ? AND step_id = ?",
            (run_id, step_id),
        ).fetchone()
        if row is None:
            raise KeyError(step_id)
        return tuple(json.loads(row["dependencies_json"]))

    def next_ready_step(self, run_id: str) -> StepRecord | None:
        steps = self.steps(run_id)
        completed = {step.step_id for step in steps if step.status == "completed"}
        for step in steps:
            if step.status != "pending":
                continue
            if set(self.dependencies(run_id, step.step_id)) <= completed:
                return step
        return None

    def claim_step(self, run_id: str, step_id: str) -> int:
        now = utc_now()
        with self._connection:
            self._connection.execute(
                """
                UPDATE steps SET status = 'running', attempts = attempts + 1, updated_at = ?
                WHERE run_id = ? AND step_id = ? AND status = 'pending'
                """,
                (now, run_id, step_id),
            )
            self._connection.execute(
                "UPDATE runs SET status = 'running', updated_at = ? WHERE run_id = ?",
                (now, run_id),
            )
        row = self._connection.execute(
            "SELECT attempts FROM steps WHERE run_id = ? AND step_id = ?", (run_id, step_id)
        ).fetchone()
        if row is None:
            raise KeyError(step_id)
        return int(row["attempts"])

    def complete_step(self, run_id: str, step_id: str, result: dict[str, object]) -> None:
        with self._connection:
            self._connection.execute(
                """
                UPDATE steps SET status = 'completed', result_json = ?, error = NULL, updated_at = ?
                WHERE run_id = ? AND step_id = ?
                """,
                (json.dumps(result, sort_keys=True), utc_now(), run_id, step_id),
            )

    def retry_step(self, run_id: str, step_id: str, error: str) -> None:
        with self._connection:
            self._connection.execute(
                """
                UPDATE steps SET status = 'pending', error = ?, updated_at = ?
                WHERE run_id = ? AND step_id = ?
                """,
                (error, utc_now(), run_id, step_id),
            )

    def fail_step(self, run_id: str, step_id: str, error: str) -> None:
        with self._connection:
            self._connection.execute(
                """
                UPDATE steps SET status = 'failed', error = ?, updated_at = ?
                WHERE run_id = ? AND step_id = ?
                """,
                (error, utc_now(), run_id, step_id),
            )
        self.set_run_status(run_id, "failed")

    def result(self, run_id: str, step_id: str) -> dict[str, object]:
        row = self._connection.execute(
            "SELECT result_json FROM steps WHERE run_id = ? AND step_id = ?",
            (run_id, step_id),
        ).fetchone()
        if row is None or row["result_json"] is None:
            raise KeyError(step_id)
        return cast(dict[str, object], json.loads(row["result_json"]))

    def trace(
        self, run_id: str, event: str, *, agent: str | None = None, **detail: object
    ) -> None:
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO traces (run_id, event, agent, detail_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, event, agent, json.dumps(detail, sort_keys=True), utc_now()),
            )

    def traces(self, run_id: str) -> tuple[dict[str, object], ...]:
        rows = self._connection.execute(
            "SELECT sequence, event, agent, detail_json, created_at FROM traces "
            "WHERE run_id = ? ORDER BY sequence",
            (run_id,),
        ).fetchall()
        return tuple(
            {
                "sequence": row["sequence"],
                "event": row["event"],
                "agent": row["agent"],
                "detail": json.loads(row["detail_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        )

    def recover_stale(self, *, before: datetime) -> int:
        """heartbeat期限を超えたrunning stepを再実行可能に戻す。"""
        with self._connection:
            cursor = self._connection.execute(
                """
                UPDATE steps SET status = 'pending', error = 'stale_lease_recovered', updated_at = ?
                WHERE status = 'running' AND updated_at < ?
                """,
                (utc_now(), before.astimezone(UTC).isoformat()),
            )
        return cursor.rowcount

    def health(self) -> dict[str, int]:
        rows = self._connection.execute(
            "SELECT status, COUNT(*) AS count FROM runs GROUP BY status"
        ).fetchall()
        result = dict.fromkeys(("pending", "running", "completed", "failed"), 0)
        result.update({row["status"]: row["count"] for row in rows})
        return result
