# -*- coding: utf-8 -*-
"""Document, entity, embedding, and analysis-result persistence."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from typing import Optional


def doc_id_for(platform: str, native_id: str) -> str:
    """Return the stable id shared by raw, clean, feature, and report layers."""
    return hashlib.sha1(f"{platform}:{native_id}".encode("utf-8")).hexdigest()[:16]


_NORM = re.compile(r"[\s@#​]+|https?://\S+|\[[^\]]{1,10}\]")


def content_cluster_id(text: str) -> str:
    """Return the MVP exact-normalized content cluster id."""
    norm = _NORM.sub("", text or "").lower()
    return hashlib.md5(norm.encode("utf-8")).hexdigest()[:16]


@dataclass
class CleanDoc:
    """Stable clean-layer contract consumed by analysis and reporting."""

    doc_id: str
    platform: str
    native_id: str
    entity_id: str
    author: str = ""
    author_followers: int = 0
    text: str = ""
    likes: int = 0
    comments: int = 0
    reposts: int = 0
    plays: int = 0
    publish_ts: str = ""
    url: str = ""
    tags: list = field(default_factory=list)
    content_cluster: str = ""
    is_complaint: bool = False
    backend: str = ""
    fetched_at: str = ""

    @classmethod
    def build(cls, *, platform, native_id, entity_id, text, **kw) -> "CleanDoc":
        return cls(
            doc_id=doc_id_for(platform, native_id),
            platform=platform,
            native_id=str(native_id),
            entity_id=entity_id,
            text=text,
            content_cluster=content_cluster_id(text),
            **kw,
        )


class DocumentRepository:
    """Repository methods for content and its derived analysis artifacts."""

    conn: sqlite3.Connection

    def add_raw(
        self,
        doc: CleanDoc,
        payload: dict,
        *,
        run_id: str = "",
        entry: str = "search",
        source_query: str = "",
    ) -> None:
        """Keep the first raw row and append every collection observation."""
        self.conn.execute(
            "INSERT OR IGNORE INTO raw(doc_id,platform,native_id,entity_id,payload,backend,fetched_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (
                doc.doc_id,
                doc.platform,
                doc.native_id,
                doc.entity_id,
                json.dumps(payload, ensure_ascii=False),
                doc.backend,
                doc.fetched_at,
            ),
        )
        self.conn.execute(
            "INSERT INTO raw_observations(run_id,doc_id,platform,native_id,entity_id,entry,source_query,"
            "payload,backend,observed_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                run_id,
                doc.doc_id,
                doc.platform,
                doc.native_id,
                doc.entity_id,
                entry,
                source_query,
                json.dumps(payload, ensure_ascii=False),
                doc.backend,
                doc.fetched_at,
            ),
        )

    def add_clean(self, doc: CleanDoc) -> bool:
        """Insert a stable document or refresh mutable metrics on an existing one."""
        values = asdict(doc)
        values["tags"] = json.dumps(values["tags"], ensure_ascii=False)
        values["is_complaint"] = int(values["is_complaint"])
        existed = self.conn.execute(
            "SELECT 1 FROM clean WHERE doc_id=?", (doc.doc_id,)
        ).fetchone() is not None
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO clean(doc_id,platform,native_id,entity_id,author,author_followers,"
            "text,likes,comments,reposts,plays,publish_ts,url,tags,content_cluster,is_complaint,backend,fetched_at)"
            " VALUES(:doc_id,:platform,:native_id,:entity_id,:author,:author_followers,:text,:likes,"
            ":comments,:reposts,:plays,:publish_ts,:url,:tags,:content_cluster,:is_complaint,:backend,:fetched_at)",
            values,
        )
        if existed:
            self.conn.execute(
                "UPDATE clean SET "
                "author=CASE WHEN COALESCE(author,'')='' AND ?<>'' THEN ? ELSE author END, "
                "author_followers=MAX(COALESCE(author_followers,0),?), "
                "text=CASE WHEN COALESCE(text,'')='' AND ?<>'' THEN ? ELSE text END, "
                "likes=MAX(COALESCE(likes,0),?), comments=MAX(COALESCE(comments,0),?), "
                "reposts=MAX(COALESCE(reposts,0),?), plays=MAX(COALESCE(plays,0),?), "
                "publish_ts=CASE WHEN COALESCE(publish_ts,'')='' AND ?<>'' THEN ? ELSE publish_ts END, "
                "url=CASE WHEN COALESCE(url,'')='' AND ?<>'' THEN ? ELSE url END, fetched_at=?, backend=? WHERE doc_id=?",
                (
                    doc.author,
                    doc.author,
                    doc.author_followers,
                    doc.text,
                    doc.text,
                    doc.likes,
                    doc.comments,
                    doc.reposts,
                    doc.plays,
                    doc.publish_ts,
                    doc.publish_ts,
                    doc.url,
                    doc.url,
                    doc.fetched_at,
                    doc.backend,
                    doc.doc_id,
                ),
            )
        self.add_entity_match(
            doc.doc_id,
            doc.entity_id,
            match_reason="direct",
            observed_at=doc.fetched_at,
        )
        self.record_engagement(doc)
        return cur.rowcount > 0

    def record_engagement(self, doc: CleanDoc) -> None:
        """Save a per-observation engagement snapshot, merging duplicate query hits."""
        observed = doc.fetched_at or doc.publish_ts or "unknown"
        self.conn.execute(
            "INSERT INTO engagement_snapshots(doc_id,observed_at,likes,comments,reposts,plays,author_followers) "
            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(doc_id,observed_at) DO UPDATE SET "
            "likes=MAX(likes,excluded.likes), comments=MAX(comments,excluded.comments), "
            "reposts=MAX(reposts,excluded.reposts), plays=MAX(plays,excluded.plays), "
            "author_followers=MAX(author_followers,excluded.author_followers)",
            (
                doc.doc_id,
                observed,
                doc.likes,
                doc.comments,
                doc.reposts,
                doc.plays,
                doc.author_followers,
            ),
        )

    def add_entity_match(
        self,
        doc_id: str,
        entity_id: str,
        *,
        match_reason: str = "alias",
        source_query: str = "",
        observed_at: str = "",
    ) -> None:
        """Persist a many-to-many document/entity match."""
        if not doc_id or not entity_id:
            return
        self.conn.execute(
            "INSERT INTO document_entities(doc_id,entity_id,match_reason,source_query,first_seen,last_seen) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(doc_id,entity_id) DO UPDATE SET "
            "match_reason=excluded.match_reason, source_query=excluded.source_query, last_seen=excluded.last_seen",
            (doc_id, entity_id, match_reason, source_query, observed_at, observed_at),
        )

    def document_exists(self, doc_id: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM clean WHERE doc_id=?", (doc_id,)
        ).fetchone() is not None

    def entities_for_doc(self, doc_id: str) -> list[str]:
        return [
            row["entity_id"]
            for row in self.conn.execute(
                "SELECT entity_id FROM document_entities WHERE doc_id=? ORDER BY entity_id",
                (doc_id,),
            )
        ]

    def clean_missing_features(self, analysis_version: str = "") -> list[sqlite3.Row]:
        if analysis_version:
            return self.conn.execute(
                "SELECT c.* FROM clean c LEFT JOIN features f USING(doc_id) "
                "WHERE f.doc_id IS NULL OR COALESCE(f.analysis_version,'')<>?",
                (analysis_version,),
            ).fetchall()
        return self.conn.execute(
            "SELECT c.* FROM clean c LEFT JOIN features f USING(doc_id) WHERE f.doc_id IS NULL"
        ).fetchall()

    def clean_missing_embedding(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT doc_id, text FROM clean WHERE embedding IS NULL AND text<>''"
        ).fetchall()

    def set_embedding(self, doc_id: str, blob: bytes) -> None:
        self.conn.execute("UPDATE clean SET embedding=? WHERE doc_id=?", (blob, doc_id))

    def get_embedding(self, doc_id: str) -> bytes | None:
        row = self.conn.execute(
            "SELECT embedding FROM clean WHERE doc_id=?", (doc_id,)
        ).fetchone()
        return row["embedding"] if row else None

    def embeddings_for(self, entity_id: str | None = None) -> list[tuple]:
        query = "SELECT doc_id, embedding FROM clean WHERE embedding IS NOT NULL"
        args: tuple = ()
        if entity_id:
            query += (
                " AND EXISTS(SELECT 1 FROM document_entities de "
                "WHERE de.doc_id=clean.doc_id AND de.entity_id=?)"
            )
            args = (entity_id,)
        return [
            (row["doc_id"], row["embedding"])
            for row in self.conn.execute(query, args)
        ]

    def add_feature(
        self,
        doc_id: str,
        feat: dict,
        *,
        analysis_version: str = "",
        engine: str = "",
        model: str = "",
        prompt_version: str = "",
        analyzed_at: str = "",
    ) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO features(doc_id,polarity,intensity,confidence,is_ironic,is_spam,"
            "topic_label,summary,evidence,signals,risk,analysis_version,engine,model,prompt_version,analyzed_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                doc_id,
                feat.get("polarity"),
                feat.get("intensity", 0.0),
                feat.get("confidence", 0.0),
                int(feat.get("is_ironic", False)),
                int(feat.get("is_spam", False)),
                feat.get("topic_label", ""),
                feat.get("summary", ""),
                feat.get("evidence", ""),
                json.dumps(feat.get("signals", {}), ensure_ascii=False),
                feat.get("risk", 0.0),
                analysis_version,
                engine,
                model,
                prompt_version,
                analyzed_at,
            ),
        )
        if analysis_version:
            self.conn.execute(
                "INSERT OR REPLACE INTO analysis_results(doc_id,analysis_version,engine,model,prompt_version,"
                "result_json,created_at) VALUES(?,?,?,?,?,?,?)",
                (
                    doc_id,
                    analysis_version,
                    engine,
                    model,
                    prompt_version,
                    json.dumps(feat, ensure_ascii=False),
                    analyzed_at,
                ),
            )

    def joined(self, entity_id: Optional[str] = None) -> list[sqlite3.Row]:
        query = (
            "SELECT c.*, f.polarity,f.intensity,f.confidence,f.is_ironic,f.topic_label,"
            "f.summary,f.evidence,f.signals,f.risk FROM clean c JOIN features f USING(doc_id)"
        )
        args = ()
        if entity_id:
            query += (
                " WHERE EXISTS(SELECT 1 FROM document_entities de "
                "WHERE de.doc_id=c.doc_id AND de.entity_id=?)"
            )
            args = (entity_id,)
        return self.conn.execute(query, args).fetchall()

    def joined_with_entities(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT c.*,de.entity_id AS matched_entity_id,de.match_reason,de.source_query,"
            "f.polarity,f.intensity,f.confidence,f.is_ironic,f.topic_label,f.summary,f.evidence,f.signals,f.risk "
            "FROM clean c JOIN features f USING(doc_id) JOIN document_entities de ON de.doc_id=c.doc_id"
        ).fetchall()
