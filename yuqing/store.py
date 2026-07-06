# -*- coding: utf-8 -*-
"""SQLite 分层存储 + 统一 doc_id 契约。

设计要点（来自立项规划）：
- doc_id 从 raw 生成后贯穿 clean→features→report 全程不变，可反查原始 payload。
- 硬去重靠 UNIQUE(platform, native_id) + INSERT OR IGNORE，幂等白送。
- content_cluster_id 软去重在入库时算一次，下游只消费不再重算。
- raw 层 append-only，永不覆盖——采集是最不可重放的一环。
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


def doc_id_for(platform: str, native_id: str) -> str:
    """确定性 doc_id：同一条帖子无论抓几次都得同一个 id（幂等地基）。"""
    return hashlib.sha1(f"{platform}:{native_id}".encode("utf-8")).hexdigest()[:16]


_NORM = re.compile(r"[\s@#​]+|https?://\S+|\[[^\]]{1,10}\]")  # 空白/@/#/零宽/链接/表情占位


def content_cluster_id(text: str) -> str:
    """软去重簇 id。

    ponytail: MVP 用"归一化后精确哈希"识别复制粘贴的水军/搬运，够挡整簇。
    真 SimHash 近似去重（洗稿）留到 Phase 2，接口不变只换实现。
    """
    norm = _NORM.sub("", text or "").lower()
    return hashlib.md5(norm.encode("utf-8")).hexdigest()[:16]


@dataclass
class CleanDoc:
    """clean 层：一条内容一行，下游分析/报告的稳定契约。"""
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
    publish_ts: str = ""          # ISO8601 (UTC+8)
    url: str = ""
    tags: list = field(default_factory=list)
    content_cluster: str = ""
    is_complaint: bool = False    # 采集层词典派生，下游不重算
    backend: str = ""             # 实际走的 agent_reach 后端，便于断链归因
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


_SCHEMA = """
CREATE TABLE IF NOT EXISTS raw (
    doc_id TEXT, platform TEXT, native_id TEXT, entity_id TEXT,
    payload TEXT, backend TEXT, fetched_at TEXT,
    UNIQUE(platform, native_id) ON CONFLICT IGNORE
);
CREATE TABLE IF NOT EXISTS clean (
    doc_id TEXT PRIMARY KEY, platform TEXT, native_id TEXT, entity_id TEXT,
    author TEXT, author_followers INTEGER, text TEXT,
    likes INTEGER, comments INTEGER, reposts INTEGER,
    publish_ts TEXT, url TEXT, tags TEXT, content_cluster TEXT,
    is_complaint INTEGER, backend TEXT, fetched_at TEXT,
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
    status TEXT, health TEXT, note TEXT, ts TEXT
);
CREATE TABLE IF NOT EXISTS review (
    doc_id TEXT, kind TEXT, verdict TEXT, note TEXT, ts TEXT
);
CREATE TABLE IF NOT EXISTS watermark (
    entity_id TEXT, platform TEXT, entry TEXT, last_ts TEXT,
    PRIMARY KEY(entity_id, platform, entry)
);
CREATE TABLE IF NOT EXISTS alerts (cluster_key TEXT, level TEXT, doc_id TEXT, summary TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS usage (day TEXT PRIMARY KEY, calls INTEGER, tokens INTEGER);
"""


class Store:
    def __init__(self, path: str | Path = "yuqing.db"):
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)

    # --- raw / clean ---
    def add_raw(self, doc: "CleanDoc", payload: dict) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO raw(doc_id,platform,native_id,entity_id,payload,backend,fetched_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (doc.doc_id, doc.platform, doc.native_id, doc.entity_id,
             json.dumps(payload, ensure_ascii=False), doc.backend, doc.fetched_at),
        )

    def add_clean(self, doc: "CleanDoc") -> bool:
        """返回 True = 新插入，False = 已存在（去重命中）。"""
        d = asdict(doc)
        d["tags"] = json.dumps(d["tags"], ensure_ascii=False)
        d["is_complaint"] = int(d["is_complaint"])
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO clean(doc_id,platform,native_id,entity_id,author,author_followers,"
            "text,likes,comments,reposts,publish_ts,url,tags,content_cluster,is_complaint,backend,fetched_at)"
            " VALUES(:doc_id,:platform,:native_id,:entity_id,:author,:author_followers,:text,:likes,"
            ":comments,:reposts,:publish_ts,:url,:tags,:content_cluster,:is_complaint,:backend,:fetched_at)",
            d,
        )
        return cur.rowcount > 0

    def clean_missing_features(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT c.* FROM clean c LEFT JOIN features f USING(doc_id) WHERE f.doc_id IS NULL"
        ).fetchall()

    # --- features ---
    def add_feature(self, doc_id: str, feat: dict) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO features(doc_id,polarity,intensity,confidence,is_ironic,is_spam,"
            "topic_label,summary,evidence,signals,risk) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (doc_id, feat.get("polarity"), feat.get("intensity", 0.0), feat.get("confidence", 0.0),
             int(feat.get("is_ironic", False)), int(feat.get("is_spam", False)),
             feat.get("topic_label", ""), feat.get("summary", ""), feat.get("evidence", ""),
             json.dumps(feat.get("signals", {}), ensure_ascii=False), feat.get("risk", 0.0)),
        )

    def joined(self, entity_id: Optional[str] = None) -> list[sqlite3.Row]:
        """clean ⋈ features，报告/打分的输入。"""
        q = ("SELECT c.*, f.polarity,f.intensity,f.confidence,f.is_ironic,f.topic_label,"
             "f.summary,f.evidence,f.signals,f.risk FROM clean c JOIN features f USING(doc_id)")
        args = ()
        if entity_id:
            q += " WHERE c.entity_id=?"
            args = (entity_id,)
        return self.conn.execute(q, args).fetchall()

    def log_run(self, run_id, platform, entity_id, n_fetched, status, health, note, ts) -> None:
        self.conn.execute(
            "INSERT INTO run_log VALUES(?,?,?,?,?,?,?,?)",
            (run_id, platform, entity_id, n_fetched, status, health, note, ts),
        )

    def platform_baseline(self, platform: str, entity_id: str) -> Optional[float]:
        """近期成功采集条数的中位数（静默失败三态判定用）。"""
        rows = self.conn.execute(
            "SELECT n_fetched FROM run_log WHERE platform=? AND entity_id=? AND status='ok'"
            " ORDER BY ts DESC LIMIT 7", (platform, entity_id)).fetchall()
        vals = sorted(r["n_fetched"] for r in rows)
        if not vals:
            return None
        return vals[len(vals) // 2]

    def save_report(self, run_id, created_at, markdown) -> None:
        self.conn.execute("INSERT OR REPLACE INTO reports VALUES(?,?,?)", (run_id, created_at, markdown))

    # --- Phase 1: 增量水位 / 预警冷却 / 成本配额 ---
    def get_watermark(self, entity_id: str, platform: str, entry: str = "search") -> Optional[str]:
        r = self.conn.execute(
            "SELECT last_ts FROM watermark WHERE entity_id=? AND platform=? AND entry=?",
            (entity_id, platform, entry)).fetchone()
        return r["last_ts"] if r else None

    def set_watermark(self, entity_id: str, platform: str, entry: str, last_ts: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO watermark VALUES(?,?,?,?)",
                          (entity_id, platform, entry, last_ts))

    def recent_alert(self, cluster_key: str, since_ts: str) -> bool:
        """冷却判定：该事件簇在 since_ts 之后是否已告警过。"""
        return self.conn.execute(
            "SELECT 1 FROM alerts WHERE cluster_key=? AND ts>=? LIMIT 1",
            (cluster_key, since_ts)).fetchone() is not None

    def record_alert(self, cluster_key: str, level: str, doc_id: str, summary: str, ts: str) -> None:
        self.conn.execute("INSERT INTO alerts VALUES(?,?,?,?,?)",
                          (cluster_key, level, doc_id, summary, ts))

    def add_usage(self, day: str, calls: int, tokens: int) -> None:
        self.conn.execute(
            "INSERT INTO usage(day,calls,tokens) VALUES(?,?,?) "
            "ON CONFLICT(day) DO UPDATE SET calls=calls+?, tokens=tokens+?",
            (day, calls, tokens, calls, tokens))

    def usage_today(self, day: str) -> tuple[int, int]:
        r = self.conn.execute("SELECT calls,tokens FROM usage WHERE day=?", (day,)).fetchone()
        return (r["calls"], r["tokens"]) if r else (0, 0)

    # --- v1-B: 人工复核队列（数据质量地基）---
    def review_queue(self, limit: int = 20, conf_lt: float = 0.6, risk_ge: float = 30.0):
        """待复核队列：机器最没把握的（低置信/反讽/高风险负面）且尚未人工复核过，按风险降序。

        ponytail: MVP 里"复核过一次即永久出队"——即便该帖后续被重新抓取评分翻成高风险
        也不会重回队列。重评后自动重入队列留待 Phase 2（需比对 review.ts 与 features 更新时点）。
        """
        return self.conn.execute(
            "SELECT c.doc_id,c.platform,c.text,c.url,f.polarity,f.confidence,f.is_ironic,f.risk "
            "FROM clean c JOIN features f USING(doc_id) "
            "LEFT JOIN review rv ON rv.doc_id=c.doc_id "
            "WHERE rv.doc_id IS NULL AND (f.confidence < ? OR f.is_ironic=1 OR f.risk >= ?) "
            "ORDER BY f.risk DESC, f.confidence ASC LIMIT ?", (conf_lt, risk_ge, limit)).fetchall()

    def pending_review_count(self, conf_lt: float = 0.6, risk_ge: float = 30.0) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM clean c JOIN features f USING(doc_id) "
            "LEFT JOIN review rv ON rv.doc_id=c.doc_id "
            "WHERE rv.doc_id IS NULL AND (f.confidence < ? OR f.is_ironic=1 OR f.risk >= ?)",
            (conf_lt, risk_ge)).fetchone()[0]

    def add_review(self, doc_id: str, verdict: str, note: str = "", ts: str = "", kind: str = "qc") -> None:
        """记录人工复核结论（verdict 如 ok/改负/改正/串味/水军/危机确认）。"""
        self.conn.execute("INSERT INTO review VALUES(?,?,?,?,?)", (doc_id, kind, verdict, note, ts))
        self.conn.commit()

    def review_stats(self) -> dict:
        """质检 KPI：已复核数 + 机器判错数（verdict!=ok）。"""
        r = self.conn.execute(
            "SELECT COUNT(*) n, SUM(CASE WHEN verdict<>'ok' THEN 1 ELSE 0 END) wrong FROM review").fetchone()
        return {"reviewed": r["n"] or 0, "machine_wrong": r["wrong"] or 0}

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.commit()
        self.conn.close()
