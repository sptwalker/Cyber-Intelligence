# -*- coding: utf-8 -*-
"""Human review, annotation, and account-registry persistence."""

from __future__ import annotations

import json
import sqlite3
from typing import Optional


class ReviewRepository:
    """Repository methods for analyst feedback and deterministic account labels."""

    conn: sqlite3.Connection

    def review_queue(
        self, limit: int = 20, conf_lt: float = 0.6, risk_ge: float = 30.0
    ):
        return self.conn.execute(
            "SELECT c.doc_id,c.platform,c.text,c.url,f.polarity,f.confidence,f.is_ironic,f.risk "
            "FROM clean c JOIN features f USING(doc_id) "
            "LEFT JOIN review rv ON rv.doc_id=c.doc_id "
            "WHERE rv.doc_id IS NULL AND (f.confidence < ? OR f.is_ironic=1 OR f.risk >= ? "
            "OR f.signals LIKE '%cross_disagree%') "
            "ORDER BY f.risk DESC, f.confidence ASC LIMIT ?",
            (conf_lt, risk_ge, limit),
        ).fetchall()

    def pending_review_count(
        self,
        conf_lt: float = 0.6,
        risk_ge: float = 30.0,
        *,
        entity_id: str | None = None,
    ) -> int:
        query = (
            "SELECT COUNT(*) FROM clean c JOIN features f USING(doc_id) "
            "LEFT JOIN review rv ON rv.doc_id=c.doc_id "
            "WHERE rv.doc_id IS NULL AND (f.confidence < ? OR f.is_ironic=1 OR f.risk >= ? "
            "OR f.signals LIKE '%cross_disagree%')"
        )
        args: list = [conf_lt, risk_ge]
        if entity_id:
            query += (
                " AND EXISTS(SELECT 1 FROM document_entities de "
                "WHERE de.doc_id=c.doc_id AND de.entity_id=?)"
            )
            args.append(entity_id)
        return self.conn.execute(query, args).fetchone()[0]

    def add_review(
        self,
        doc_id: str,
        verdict: str,
        note: str = "",
        ts: str = "",
        kind: str = "qc",
        actor: str = "",
        *,
        commit: bool = True,
    ) -> None:
        self.conn.execute(
            "INSERT INTO review(doc_id,kind,verdict,note,ts,actor) VALUES(?,?,?,?,?,?)",
            (doc_id, kind, verdict, note, ts, actor),
        )
        if commit:
            self.conn.commit()

    def review_stats(self) -> dict:
        row = self.conn.execute(
            "SELECT COUNT(*) n, SUM(CASE WHEN verdict<>'ok' THEN 1 ELSE 0 END) wrong FROM review"
        ).fetchone()
        return {"reviewed": row["n"] or 0, "machine_wrong": row["wrong"] or 0}

    def add_annotation(
        self,
        doc_id: str,
        *,
        subject: Optional[str] = None,
        stance: Optional[str] = None,
        importance: Optional[str] = None,
        picked_words: Optional[list] = None,
        note: str = "",
        sample_source: str = "manual",
        entity_id: Optional[str] = None,
        ts: str = "",
        annotator: str = "local",
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO annotations(doc_id,subject,stance,importance,picked_words,note,"
            "annotator,sample_source,entity_id,ts) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                doc_id,
                subject,
                stance,
                importance,
                json.dumps(picked_words or [], ensure_ascii=False),
                note,
                annotator,
                sample_source,
                entity_id,
                ts,
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    def annotation_candidates(
        self, entity_id: Optional[str] = None, limit: int = 200
    ):
        query = (
            "SELECT c.doc_id,c.platform,c.author,c.author_followers,c.text,c.url,c.publish_ts,"
            "c.embedding,c.entity_id,f.polarity,f.confidence,f.signals,f.risk "
            "FROM clean c JOIN features f USING(doc_id) "
            "LEFT JOIN annotations a ON a.doc_id=c.doc_id WHERE a.doc_id IS NULL"
        )
        args: tuple = ()
        if entity_id:
            query += (
                " AND EXISTS(SELECT 1 FROM document_entities de "
                "WHERE de.doc_id=c.doc_id AND de.entity_id=?)"
            )
            args = (entity_id,)
        query += " ORDER BY f.confidence ASC, f.risk DESC LIMIT ?"
        return self.conn.execute(query, args + (limit,)).fetchall()

    def annotated_count(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(DISTINCT doc_id) FROM annotations"
        ).fetchone()[0]

    def latest_annotation(self, doc_id: str):
        return self.conn.execute(
            "SELECT * FROM annotations WHERE doc_id=? ORDER BY id DESC LIMIT 1",
            (doc_id,),
        ).fetchone()

    def load_annotations(self, entity_id: Optional[str] = None):
        query = (
            "SELECT a.doc_id, a.subject, a.stance, a.importance, a.picked_words, "
            "c.text, c.author_followers, c.embedding FROM annotations a "
            "JOIN clean c USING(doc_id) WHERE a.subject IS NOT NULL "
            "AND a.id IN (SELECT MAX(id) FROM annotations GROUP BY doc_id)"
        )
        args: tuple = ()
        if entity_id:
            query += (
                " AND EXISTS(SELECT 1 FROM document_entities de "
                "WHERE de.doc_id=c.doc_id AND de.entity_id=?)"
            )
            args = (entity_id,)
        return self.conn.execute(query, args).fetchall()

    def add_account(
        self,
        author: str,
        subject_type: str,
        *,
        platform: str = "",
        entity_id: Optional[str] = None,
        note: str = "",
        ts: str = "",
    ) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO account_registry(platform,author,subject_type,entity_id,note,ts) "
            "VALUES(?,?,?,?,?,?)",
            (platform, author, subject_type, entity_id, note, ts),
        )
        self.conn.commit()

    def account_type(self, author: str, platform: str = "") -> Optional[str]:
        if not author:
            return None
        row = self.conn.execute(
            "SELECT subject_type FROM account_registry WHERE author=? AND (platform=? OR platform='') "
            "ORDER BY CASE WHEN platform=? THEN 0 ELSE 1 END LIMIT 1",
            (author, platform, platform),
        ).fetchone()
        return row["subject_type"] if row else None

    def list_accounts(self, entity_id: Optional[str] = None):
        query = "SELECT * FROM account_registry"
        args: tuple = ()
        if entity_id:
            query += " WHERE entity_id IS ? OR entity_id IS NULL"
            args = (entity_id,)
        return self.conn.execute(
            query + " ORDER BY subject_type, author", args
        ).fetchall()

    def delete_account(self, account_id: int) -> bool:
        cur = self.conn.execute(
            "DELETE FROM account_registry WHERE id=?", (account_id,)
        )
        self.conn.commit()
        return cur.rowcount > 0
