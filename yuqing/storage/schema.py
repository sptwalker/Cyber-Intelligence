# -*- coding: utf-8 -*-
"""SQLite schema creation and backward-compatible migrations."""

from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw (
    doc_id TEXT, platform TEXT, native_id TEXT, entity_id TEXT,
    payload TEXT, backend TEXT, fetched_at TEXT,
    UNIQUE(platform, native_id) ON CONFLICT IGNORE
);
CREATE TABLE IF NOT EXISTS clean (
    doc_id TEXT PRIMARY KEY, platform TEXT, native_id TEXT, entity_id TEXT,
    author TEXT, author_followers INTEGER, text TEXT,
    likes INTEGER, comments INTEGER, reposts INTEGER, plays INTEGER,
    publish_ts TEXT, url TEXT, tags TEXT, content_cluster TEXT,
    is_complaint INTEGER, backend TEXT, fetched_at TEXT, embedding BLOB,
    UNIQUE(platform, native_id) ON CONFLICT IGNORE
);
CREATE TABLE IF NOT EXISTS features (
    doc_id TEXT PRIMARY KEY, polarity TEXT, intensity REAL, confidence REAL,
    is_ironic INTEGER, is_spam INTEGER, topic_label TEXT, summary TEXT,
    evidence TEXT, signals TEXT, risk REAL,
    FOREIGN KEY(doc_id) REFERENCES clean(doc_id)
);
CREATE TABLE IF NOT EXISTS reports (run_id TEXT PRIMARY KEY, created_at TEXT, markdown TEXT);
CREATE TABLE IF NOT EXISTS run_log (
    run_id TEXT, platform TEXT, entity_id TEXT, n_fetched INTEGER,
    status TEXT, health TEXT, note TEXT, ts TEXT, entry TEXT, source_query TEXT
);
CREATE TABLE IF NOT EXISTS review (
    doc_id TEXT, kind TEXT, verdict TEXT, note TEXT, ts TEXT, actor TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_review_doc ON review(doc_id);
CREATE TABLE IF NOT EXISTS annotations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id TEXT NOT NULL,
    subject TEXT,
    stance TEXT,
    importance TEXT,
    picked_words TEXT,
    note TEXT,
    annotator TEXT DEFAULT 'local',
    sample_source TEXT,
    entity_id TEXT,
    ts TEXT
);
CREATE INDEX IF NOT EXISTS idx_ann_doc ON annotations(doc_id);
CREATE TABLE IF NOT EXISTS account_registry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT,
    author TEXT NOT NULL,
    subject_type TEXT NOT NULL,
    entity_id TEXT,
    note TEXT, ts TEXT,
    UNIQUE(platform, author)
);
CREATE TABLE IF NOT EXISTS watermark (
    entity_id TEXT, platform TEXT, entry TEXT, last_ts TEXT,
    PRIMARY KEY(entity_id, platform, entry)
);
CREATE TABLE IF NOT EXISTS alerts (cluster_key TEXT, level TEXT, doc_id TEXT, summary TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS usage (day TEXT PRIMARY KEY, calls INTEGER, tokens INTEGER);
CREATE TABLE IF NOT EXISTS heartbeat (
    id INTEGER PRIMARY KEY, last_start TEXT, last_success TEXT, last_status TEXT, note TEXT
);
CREATE TABLE IF NOT EXISTS raw_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT, doc_id TEXT, platform TEXT, native_id TEXT, entity_id TEXT,
    entry TEXT, source_query TEXT, payload TEXT, backend TEXT, observed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_raw_obs_doc ON raw_observations(doc_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_raw_obs_run ON raw_observations(run_id, platform, entity_id);
CREATE TABLE IF NOT EXISTS document_entities (
    doc_id TEXT NOT NULL, entity_id TEXT NOT NULL,
    match_reason TEXT, source_query TEXT, first_seen TEXT, last_seen TEXT,
    PRIMARY KEY(doc_id, entity_id),
    FOREIGN KEY(doc_id) REFERENCES clean(doc_id)
);
CREATE INDEX IF NOT EXISTS idx_doc_entities_entity ON document_entities(entity_id, doc_id);
CREATE TABLE IF NOT EXISTS engagement_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id TEXT NOT NULL, observed_at TEXT NOT NULL,
    likes INTEGER, comments INTEGER, reposts INTEGER, plays INTEGER, author_followers INTEGER,
    UNIQUE(doc_id, observed_at),
    FOREIGN KEY(doc_id) REFERENCES clean(doc_id)
);
CREATE INDEX IF NOT EXISTS idx_engagement_doc ON engagement_snapshots(doc_id, observed_at);
CREATE TABLE IF NOT EXISTS analysis_results (
    doc_id TEXT NOT NULL, analysis_version TEXT NOT NULL,
    engine TEXT, model TEXT, prompt_version TEXT, result_json TEXT, created_at TEXT,
    PRIMARY KEY(doc_id, analysis_version),
    FOREIGN KEY(doc_id) REFERENCES clean(doc_id)
);
CREATE TABLE IF NOT EXISTS incidents (
    incident_id TEXT PRIMARY KEY, entity_id TEXT, cluster_key TEXT, level TEXT,
    status TEXT, doc_id TEXT, summary TEXT,
    created_at TEXT, updated_at TEXT, actor TEXT, note TEXT
);
CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status, created_at);
CREATE INDEX IF NOT EXISTS idx_incidents_cluster ON incidents(entity_id, cluster_key, created_at);
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY, value TEXT, updated_at TEXT
);
"""

MIGRATIONS = (
    "ALTER TABLE clean ADD COLUMN plays INTEGER DEFAULT 0",
    "ALTER TABLE clean ADD COLUMN embedding BLOB",
    "ALTER TABLE features ADD COLUMN analysis_version TEXT DEFAULT ''",
    "ALTER TABLE features ADD COLUMN engine TEXT DEFAULT ''",
    "ALTER TABLE features ADD COLUMN model TEXT DEFAULT ''",
    "ALTER TABLE features ADD COLUMN prompt_version TEXT DEFAULT ''",
    "ALTER TABLE features ADD COLUMN analyzed_at TEXT DEFAULT ''",
    "ALTER TABLE run_log ADD COLUMN entry TEXT DEFAULT ''",
    "ALTER TABLE run_log ADD COLUMN source_query TEXT DEFAULT ''",
    "ALTER TABLE review ADD COLUMN actor TEXT DEFAULT ''",
)


def initialize_schema(conn: sqlite3.Connection) -> None:
    """Create current tables, migrate legacy databases, and persist schema metadata."""
    conn.executescript(SCHEMA)
    for ddl in MIGRATIONS:
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError:
            pass

    # clean.entity_id remains a legacy read-path fallback; seed the relation table once.
    conn.execute(
        "INSERT OR IGNORE INTO document_entities(doc_id,entity_id,match_reason,source_query,first_seen,last_seen) "
        "SELECT doc_id,entity_id,'legacy','',fetched_at,fetched_at FROM clean "
        "WHERE entity_id IS NOT NULL AND entity_id<>''"
    )
    conn.execute(
        "INSERT OR REPLACE INTO schema_meta(key,value,updated_at) VALUES('schema_version',?,datetime('now'))",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()


class SchemaRepository:
    """Read access to persisted schema metadata."""

    conn: sqlite3.Connection

    def schema_version(self) -> int:
        row = self.conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()
        return int(row["value"]) if row else 0
