# -*- coding: utf-8 -*-
"""Run, report, alert, incident, quota, and heartbeat persistence."""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Optional


class OperationsRepository:
    """Repository methods for operational state and workflows."""

    conn: sqlite3.Connection

    def log_run(
        self,
        run_id,
        platform,
        entity_id,
        n_fetched,
        status,
        health,
        note,
        ts,
        entry: str = "",
        source_query: str = "",
    ) -> None:
        self.conn.execute(
            "INSERT INTO run_log(run_id,platform,entity_id,n_fetched,status,health,note,ts,entry,source_query) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                run_id,
                platform,
                entity_id,
                n_fetched,
                status,
                health,
                note,
                ts,
                entry,
                source_query,
            ),
        )

    def platform_baseline(
        self,
        platform: str,
        entity_id: str,
        *,
        entry: str = "",
        source_query: str = "",
    ) -> Optional[float]:
        query = (
            "SELECT n_fetched FROM run_log "
            "WHERE platform=? AND entity_id=? AND status='ok'"
        )
        args: list = [platform, entity_id]
        if entry:
            query += " AND entry=?"
            args.append(entry)
        if source_query:
            query += " AND source_query=?"
            args.append(source_query)
        rows = self.conn.execute(query + " ORDER BY ts DESC LIMIT 7", args).fetchall()
        values = sorted(row["n_fetched"] for row in rows)
        if not values:
            return None
        return values[len(values) // 2]

    def save_report(self, run_id, created_at, markdown) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO reports VALUES(?,?,?)",
            (run_id, created_at, markdown),
        )

    def get_watermark(
        self, entity_id: str, platform: str, entry: str = "search"
    ) -> Optional[str]:
        row = self.conn.execute(
            "SELECT last_ts FROM watermark WHERE entity_id=? AND platform=? AND entry=?",
            (entity_id, platform, entry),
        ).fetchone()
        return row["last_ts"] if row else None

    def set_watermark(
        self, entity_id: str, platform: str, entry: str, last_ts: str
    ) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO watermark VALUES(?,?,?,?)",
            (entity_id, platform, entry, last_ts),
        )

    def recent_alert(self, cluster_key: str, since_ts: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM alerts WHERE cluster_key=? AND ts>=? LIMIT 1",
            (cluster_key, since_ts),
        ).fetchone() is not None

    def record_alert(
        self, cluster_key: str, level: str, doc_id: str, summary: str, ts: str
    ) -> None:
        self.conn.execute(
            "INSERT INTO alerts VALUES(?,?,?,?,?)",
            (cluster_key, level, doc_id, summary, ts),
        )

    def create_incident(
        self,
        *,
        entity_id: str,
        cluster_key: str,
        level: str,
        doc_id: str,
        summary: str,
        ts: str,
    ) -> dict:
        incident_id = hashlib.sha1(
            f"{entity_id}:{cluster_key}:{ts}".encode("utf-8")
        ).hexdigest()[:20]
        self.conn.execute(
            "INSERT OR IGNORE INTO incidents(incident_id,entity_id,cluster_key,level,status,doc_id,summary,"
            "created_at,updated_at,actor,note) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                incident_id,
                entity_id,
                cluster_key,
                level,
                "pending_confirmation",
                doc_id,
                summary,
                ts,
                ts,
                "system",
                "",
            ),
        )
        return dict(
            self.conn.execute(
                "SELECT * FROM incidents WHERE incident_id=?", (incident_id,)
            ).fetchone()
        )

    def get_incident(self, incident_id: str):
        row = self.conn.execute(
            "SELECT * FROM incidents WHERE incident_id=?", (incident_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_incidents(
        self, status: Optional[str] = None, limit: int = 100
    ) -> list[dict]:
        query, args = "SELECT * FROM incidents", ()
        if status:
            query += " WHERE status=?"
            args = (status,)
        query += " ORDER BY created_at DESC LIMIT ?"
        return [
            dict(row)
            for row in self.conn.execute(query, args + (limit,)).fetchall()
        ]

    def transition_incident(
        self,
        incident_id: str,
        status: str,
        *,
        actor: str,
        note: str = "",
        ts: str = "",
    ) -> bool:
        current = self.get_incident(incident_id)
        transitions = {
            "pending_confirmation": {"confirmed", "suppressed"},
            "confirmed": {"escalated", "resolved"},
            "escalated": {"resolved"},
        }
        if not current or status not in transitions.get(current["status"], set()):
            return False
        cur = self.conn.execute(
            "UPDATE incidents SET status=?,updated_at=?,actor=?,note=? WHERE incident_id=?",
            (status, ts, actor, note, incident_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def add_usage(self, day: str, calls: int, tokens: int) -> None:
        self.conn.execute(
            "INSERT INTO usage(day,calls,tokens) VALUES(?,?,?) "
            "ON CONFLICT(day) DO UPDATE SET calls=calls+?, tokens=tokens+?",
            (day, calls, tokens, calls, tokens),
        )

    def usage_today(self, day: str) -> tuple[int, int]:
        row = self.conn.execute(
            "SELECT calls,tokens FROM usage WHERE day=?", (day,)
        ).fetchone()
        return (row["calls"], row["tokens"]) if row else (0, 0)

    def record_heartbeat(self, ts: str, status: str, note: str = "") -> None:
        prev = self.conn.execute(
            "SELECT last_success FROM heartbeat WHERE id=1"
        ).fetchone()
        last_success = ts if status == "ok" else (prev["last_success"] if prev else "")
        self.conn.execute(
            "INSERT OR REPLACE INTO heartbeat(id,last_start,last_success,last_status,note) "
            "VALUES(1,?,?,?,?)",
            (ts, last_success, status, note),
        )
        self.conn.commit()

    def get_heartbeat(self) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT last_start,last_success,last_status,note FROM heartbeat WHERE id=1"
        ).fetchone()
        return dict(row) if row else None
