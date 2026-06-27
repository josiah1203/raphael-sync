"""Sync session state — Postgres when configured, SQLite fallback for tests."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class SyncStore:
    def __init__(self, db_path: Path | None = None) -> None:
        from raphael_contracts import db as rdb

        self._postgres = rdb.is_postgres()
        if self._postgres:
            rdb.ensure_migrations()
        else:
            self._db = db_path or Path(os.environ.get("RAPHAEL_SYNC_DB", "/tmp/raphael-sync.db"))
            self._db.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self._db, check_same_thread=False)
            self._init_sqlite()

    def _init_sqlite(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sync_global_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                status TEXT NOT NULL DEFAULT 'synced',
                queued_events INTEGER NOT NULL DEFAULT 0,
                last_sync TEXT
            );
            INSERT OR IGNORE INTO sync_global_state (id, status, queued_events, last_sync)
            VALUES (1, 'synced', 0, NULL);

            CREATE TABLE IF NOT EXISTS sync_sessions (
                session_id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'pending',
                queued_events INTEGER NOT NULL DEFAULT 0,
                last_sync TEXT,
                committed_at TEXT,
                events_accepted INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            """
        )
        self._conn.commit()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def get_status(self) -> dict[str, Any]:
        if self._postgres:
            from raphael_contracts import db as rdb

            row = rdb.pg_fetchone(
                "SELECT status, queued_events, last_sync FROM sync_global_state WHERE id = 1"
            )
        else:
            row = self._conn.execute(
                "SELECT status, queued_events, last_sync FROM sync_global_state WHERE id = 1"
            ).fetchone()
        if not row:
            return {"status": "synced", "queued_events": 0, "last_sync": self._now()}
        if isinstance(row, dict):
            last_sync = row.get("last_sync")
            return {
                "status": row["status"],
                "queued_events": int(row["queued_events"] or 0),
                "last_sync": str(last_sync) if last_sync else self._now(),
            }
        return {
            "status": row[0],
            "queued_events": int(row[1] or 0),
            "last_sync": row[2] or self._now(),
        }

    def push_events(self, events: list[Any]) -> dict[str, Any]:
        now = self._now()
        status = self.get_status()
        queued = max(0, status["queued_events"] - len(events))
        if self._postgres:
            from raphael_contracts import db as rdb

            rdb.pg_execute(
                "UPDATE sync_global_state SET status = %s, queued_events = %s, last_sync = %s WHERE id = 1",
                ("synced", queued, now),
            )
        else:
            self._conn.execute(
                "UPDATE sync_global_state SET status = ?, queued_events = ?, last_sync = ? WHERE id = 1",
                ("synced", queued, now),
            )
            self._conn.commit()
        return {"accepted": len(events), "status": "synced", "queued_events": queued, "last_sync": now}

    def commit_session(self, session_id: str, events: list[Any]) -> dict[str, Any]:
        now = self._now()
        accepted = len(events)
        if self._postgres:
            from raphael_contracts import db as rdb

            rdb.pg_execute(
                """
                INSERT INTO sync_sessions
                    (session_id, status, queued_events, last_sync, committed_at, events_accepted, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (session_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    last_sync = EXCLUDED.last_sync,
                    committed_at = EXCLUDED.committed_at,
                    events_accepted = EXCLUDED.events_accepted
                """,
                (session_id, "committed", 0, now, now, accepted, now),
            )
        else:
            self._conn.execute(
                """
                INSERT INTO sync_sessions
                    (session_id, status, queued_events, last_sync, committed_at, events_accepted, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    status = excluded.status,
                    last_sync = excluded.last_sync,
                    committed_at = excluded.committed_at,
                    events_accepted = excluded.events_accepted
                """,
                (session_id, "committed", 0, now, now, accepted, now),
            )
            self._conn.commit()
        return {
            "session_id": session_id,
            "status": "committed",
            "committed_at": now,
            "events_accepted": accepted,
        }

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        if self._postgres:
            from raphael_contracts import db as rdb

            row = rdb.pg_fetchone(
                "SELECT session_id, status, queued_events, last_sync, committed_at, events_accepted "
                "FROM sync_sessions WHERE session_id = %s",
                (session_id,),
            )
        else:
            row = self._conn.execute(
                "SELECT session_id, status, queued_events, last_sync, committed_at, events_accepted "
                "FROM sync_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if not row:
            return None
        if isinstance(row, dict):
            return dict(row)
        return {
            "session_id": row[0],
            "status": row[1],
            "queued_events": row[2],
            "last_sync": row[3],
            "committed_at": row[4],
            "events_accepted": row[5],
        }
