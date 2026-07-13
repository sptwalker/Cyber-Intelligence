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

SCHEMA_VERSION = 2


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
    plays: int = 0                # 播放量（B站 score / 抖音 plays）——视频平台的核心传播信号
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
    subject TEXT,          -- 主体：官方/准官方/媒体/用户·KOL（枚举见 classify.SUBJECTS）
    stance TEXT,           -- 立场：赞扬/中立/批评/吐槽/投诉/纯传播（classify.STANCES）
    importance TEXT,       -- 重要性：高/中/低
    picked_words TEXT,     -- JSON: [{"word","role","span":[s,e]}]，role 取自 keywords.TAGS
    note TEXT,
    annotator TEXT DEFAULT 'local',
    sample_source TEXT,    -- active/manual/queue：样本来自主动学习队列还是直链
    entity_id TEXT,        -- 冗余自 clean，便于按实体统计
    ts TEXT
);
CREATE INDEX IF NOT EXISTS idx_ann_doc ON annotations(doc_id);
CREATE TABLE IF NOT EXISTS account_registry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT,             -- 平台（空=跨平台匹配）
    author TEXT NOT NULL,      -- 账号昵称/handle（与 clean.author 对齐）
    subject_type TEXT NOT NULL,-- 官方/准官方/媒体（用户·KOL 是默认兜底，不入表）
    entity_id TEXT,            -- 归属实体（空=全局，如通用媒体号）
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


class Store:
    def __init__(self, path: str | Path = "yuqing.db"):
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        # 并发写等锁而非立刻报错：看板(多线程/轮询)与跑批同时写 yuqing.db 时防 "database is locked"。
        self.conn.execute("PRAGMA busy_timeout=15000")
        # WAL：允许"看板/CLI 读"与"跑批写"并发（单写者仍是多用户上限，Phase2 迁 Postgres）
        if str(path) != ":memory:":
            self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(_SCHEMA)
        migrations = [
            ("clean.plays", "ALTER TABLE clean ADD COLUMN plays INTEGER DEFAULT 0"),
            ("clean.embedding", "ALTER TABLE clean ADD COLUMN embedding BLOB"),
            ("features.analysis_version", "ALTER TABLE features ADD COLUMN analysis_version TEXT DEFAULT ''"),
            ("features.engine", "ALTER TABLE features ADD COLUMN engine TEXT DEFAULT ''"),
            ("features.model", "ALTER TABLE features ADD COLUMN model TEXT DEFAULT ''"),
            ("features.prompt_version", "ALTER TABLE features ADD COLUMN prompt_version TEXT DEFAULT ''"),
            ("features.analyzed_at", "ALTER TABLE features ADD COLUMN analyzed_at TEXT DEFAULT ''"),
            ("run_log.entry", "ALTER TABLE run_log ADD COLUMN entry TEXT DEFAULT ''"),
            ("run_log.source_query", "ALTER TABLE run_log ADD COLUMN source_query TEXT DEFAULT ''"),
            ("review.actor", "ALTER TABLE review ADD COLUMN actor TEXT DEFAULT ''"),
        ]
        for _name, ddl in migrations:
            try:                          # 轻量迁移：旧库补列（plays 播放量 / embedding 语义向量）
                self.conn.execute(ddl)
            except sqlite3.OperationalError:
                pass                      # 列已存在
        # 兼容旧库：把 clean.entity_id 回填为多对多关系的首批数据。clean.entity_id 暂留作旧读路径兜底。
        self.conn.execute(
            "INSERT OR IGNORE INTO document_entities(doc_id,entity_id,match_reason,source_query,first_seen,last_seen) "
            "SELECT doc_id,entity_id,'legacy','',fetched_at,fetched_at FROM clean "
            "WHERE entity_id IS NOT NULL AND entity_id<>''"
        )
        self.conn.execute(
            "INSERT OR REPLACE INTO schema_meta(key,value,updated_at) VALUES('schema_version',?,datetime('now'))",
            (str(SCHEMA_VERSION),),
        )
        self.conn.commit()

    def schema_version(self) -> int:
        r = self.conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
        return int(r["value"]) if r else 0

    # --- raw / clean ---
    def add_raw(self, doc: "CleanDoc", payload: dict, *, run_id: str = "",
                entry: str = "search", source_query: str = "") -> None:
        """保留兼容 raw 首见记录，同时把每次采集写入 append-only raw_observations。"""
        self.conn.execute(
            "INSERT OR IGNORE INTO raw(doc_id,platform,native_id,entity_id,payload,backend,fetched_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (doc.doc_id, doc.platform, doc.native_id, doc.entity_id,
             json.dumps(payload, ensure_ascii=False), doc.backend, doc.fetched_at),
        )
        self.conn.execute(
            "INSERT INTO raw_observations(run_id,doc_id,platform,native_id,entity_id,entry,source_query,"
            "payload,backend,observed_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (run_id, doc.doc_id, doc.platform, doc.native_id, doc.entity_id, entry, source_query,
             json.dumps(payload, ensure_ascii=False), doc.backend, doc.fetched_at),
        )

    def add_clean(self, doc: "CleanDoc") -> bool:
        """插入稳定文档并刷新可变指标；返回 True=首次出现，False=已存在。"""
        d = asdict(doc)
        d["tags"] = json.dumps(d["tags"], ensure_ascii=False)
        d["is_complaint"] = int(d["is_complaint"])
        existed = self.conn.execute("SELECT 1 FROM clean WHERE doc_id=?", (doc.doc_id,)).fetchone() is not None
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO clean(doc_id,platform,native_id,entity_id,author,author_followers,"
            "text,likes,comments,reposts,plays,publish_ts,url,tags,content_cluster,is_complaint,backend,fetched_at)"
            " VALUES(:doc_id,:platform,:native_id,:entity_id,:author,:author_followers,:text,:likes,"
            ":comments,:reposts,:plays,:publish_ts,:url,:tags,:content_cluster,:is_complaint,:backend,:fetched_at)",
            d,
        )
        if existed:
            # 社交互动会持续增长；保留非空正文/链接并用较大互动值刷新当前读模型。
            self.conn.execute(
                "UPDATE clean SET "
                "author=CASE WHEN COALESCE(author,'')='' AND ?<>'' THEN ? ELSE author END, "
                "author_followers=MAX(COALESCE(author_followers,0),?), "
                "text=CASE WHEN COALESCE(text,'')='' AND ?<>'' THEN ? ELSE text END, "
                "likes=MAX(COALESCE(likes,0),?), comments=MAX(COALESCE(comments,0),?), "
                "reposts=MAX(COALESCE(reposts,0),?), plays=MAX(COALESCE(plays,0),?), "
                "publish_ts=CASE WHEN COALESCE(publish_ts,'')='' AND ?<>'' THEN ? ELSE publish_ts END, "
                "url=CASE WHEN COALESCE(url,'')='' AND ?<>'' THEN ? ELSE url END, fetched_at=?, backend=? WHERE doc_id=?",
                (doc.author, doc.author, doc.author_followers, doc.text, doc.text,
                 doc.likes, doc.comments, doc.reposts, doc.plays,
                 doc.publish_ts, doc.publish_ts, doc.url, doc.url,
                 doc.fetched_at, doc.backend, doc.doc_id),
            )
        self.add_entity_match(doc.doc_id, doc.entity_id, match_reason="direct",
                              observed_at=doc.fetched_at)
        self.record_engagement(doc)
        return cur.rowcount > 0

    def record_engagement(self, doc: "CleanDoc") -> None:
        """按观测时间保存互动快照；同一轮多搜索词命中时合并为最大值。"""
        observed = doc.fetched_at or doc.publish_ts or "unknown"
        self.conn.execute(
            "INSERT INTO engagement_snapshots(doc_id,observed_at,likes,comments,reposts,plays,author_followers) "
            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(doc_id,observed_at) DO UPDATE SET "
            "likes=MAX(likes,excluded.likes), comments=MAX(comments,excluded.comments), "
            "reposts=MAX(reposts,excluded.reposts), plays=MAX(plays,excluded.plays), "
            "author_followers=MAX(author_followers,excluded.author_followers)",
            (doc.doc_id, observed, doc.likes, doc.comments, doc.reposts, doc.plays, doc.author_followers),
        )

    def add_entity_match(self, doc_id: str, entity_id: str, *, match_reason: str = "alias",
                         source_query: str = "", observed_at: str = "") -> None:
        """记录帖子与监控实体的多对多归属；重复命中只刷新来源与 last_seen。"""
        if not doc_id or not entity_id:
            return
        self.conn.execute(
            "INSERT INTO document_entities(doc_id,entity_id,match_reason,source_query,first_seen,last_seen) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(doc_id,entity_id) DO UPDATE SET "
            "match_reason=excluded.match_reason, source_query=excluded.source_query, last_seen=excluded.last_seen",
            (doc_id, entity_id, match_reason, source_query, observed_at, observed_at),
        )

    def document_exists(self, doc_id: str) -> bool:
        return self.conn.execute("SELECT 1 FROM clean WHERE doc_id=?", (doc_id,)).fetchone() is not None

    def entities_for_doc(self, doc_id: str) -> list[str]:
        return [r["entity_id"] for r in self.conn.execute(
            "SELECT entity_id FROM document_entities WHERE doc_id=? ORDER BY entity_id", (doc_id,))]

    def clean_missing_features(self, analysis_version: str = "") -> list[sqlite3.Row]:
        if analysis_version:
            return self.conn.execute(
                "SELECT c.* FROM clean c LEFT JOIN features f USING(doc_id) "
                "WHERE f.doc_id IS NULL OR COALESCE(f.analysis_version,'')<>?", (analysis_version,)
            ).fetchall()
        return self.conn.execute(
            "SELECT c.* FROM clean c LEFT JOIN features f USING(doc_id) WHERE f.doc_id IS NULL"
        ).fetchall()

    # --- 语义向量（embedding，缓存：同 doc 只算一次）---
    def clean_missing_embedding(self) -> list[sqlite3.Row]:
        """有正文但还没算向量的 clean 帖（供批量向量化）。"""
        return self.conn.execute(
            "SELECT doc_id, text FROM clean WHERE embedding IS NULL AND text<>''").fetchall()

    def set_embedding(self, doc_id: str, blob: bytes) -> None:
        self.conn.execute("UPDATE clean SET embedding=? WHERE doc_id=?", (blob, doc_id))

    def get_embedding(self, doc_id: str) -> bytes | None:
        r = self.conn.execute("SELECT embedding FROM clean WHERE doc_id=?", (doc_id,)).fetchone()
        return r["embedding"] if r else None

    def embeddings_for(self, entity_id: str | None = None) -> list[tuple]:
        """[(doc_id, blob), ...] 已算向量的帖，供检索/聚类。entity_id=None 取全部。"""
        q = "SELECT doc_id, embedding FROM clean WHERE embedding IS NOT NULL"
        args: tuple = ()
        if entity_id:
            q += (" AND EXISTS(SELECT 1 FROM document_entities de "
                  "WHERE de.doc_id=clean.doc_id AND de.entity_id=?)")
            args = (entity_id,)
        return [(r["doc_id"], r["embedding"]) for r in self.conn.execute(q, args)]

    # --- features ---
    def add_feature(self, doc_id: str, feat: dict, *, analysis_version: str = "",
                    engine: str = "", model: str = "", prompt_version: str = "",
                    analyzed_at: str = "") -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO features(doc_id,polarity,intensity,confidence,is_ironic,is_spam,"
            "topic_label,summary,evidence,signals,risk,analysis_version,engine,model,prompt_version,analyzed_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (doc_id, feat.get("polarity"), feat.get("intensity", 0.0), feat.get("confidence", 0.0),
             int(feat.get("is_ironic", False)), int(feat.get("is_spam", False)),
             feat.get("topic_label", ""), feat.get("summary", ""), feat.get("evidence", ""),
             json.dumps(feat.get("signals", {}), ensure_ascii=False), feat.get("risk", 0.0),
             analysis_version, engine, model, prompt_version, analyzed_at),
        )
        if analysis_version:
            self.conn.execute(
                "INSERT OR REPLACE INTO analysis_results(doc_id,analysis_version,engine,model,prompt_version,"
                "result_json,created_at) VALUES(?,?,?,?,?,?,?)",
                (doc_id, analysis_version, engine, model, prompt_version,
                 json.dumps(feat, ensure_ascii=False), analyzed_at),
            )

    def joined(self, entity_id: Optional[str] = None) -> list[sqlite3.Row]:
        """clean ⋈ features，报告/打分的输入。"""
        q = ("SELECT c.*, f.polarity,f.intensity,f.confidence,f.is_ironic,f.topic_label,"
             "f.summary,f.evidence,f.signals,f.risk FROM clean c JOIN features f USING(doc_id)")
        args = ()
        if entity_id:
            q += (" WHERE EXISTS(SELECT 1 FROM document_entities de "
                  "WHERE de.doc_id=c.doc_id AND de.entity_id=?)")
            args = (entity_id,)
        return self.conn.execute(q, args).fetchall()

    def joined_with_entities(self) -> list[sqlite3.Row]:
        """每个 doc×entity 关系一行，供 SOV/告警等实体归属敏感场景。"""
        return self.conn.execute(
            "SELECT c.*,de.entity_id AS matched_entity_id,de.match_reason,de.source_query,"
            "f.polarity,f.intensity,f.confidence,f.is_ironic,f.topic_label,f.summary,f.evidence,f.signals,f.risk "
            "FROM clean c JOIN features f USING(doc_id) JOIN document_entities de ON de.doc_id=c.doc_id"
        ).fetchall()

    def log_run(self, run_id, platform, entity_id, n_fetched, status, health, note, ts,
                entry: str = "", source_query: str = "") -> None:
        self.conn.execute(
            "INSERT INTO run_log(run_id,platform,entity_id,n_fetched,status,health,note,ts,entry,source_query) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (run_id, platform, entity_id, n_fetched, status, health, note, ts, entry, source_query),
        )

    def platform_baseline(self, platform: str, entity_id: str, *, entry: str = "",
                          source_query: str = "") -> Optional[float]:
        """近期成功采集条数的中位数（静默失败三态判定用）。"""
        q = "SELECT n_fetched FROM run_log WHERE platform=? AND entity_id=? AND status='ok'"
        args: list = [platform, entity_id]
        if entry:
            q += " AND entry=?"; args.append(entry)
        if source_query:
            q += " AND source_query=?"; args.append(source_query)
        rows = self.conn.execute(q + " ORDER BY ts DESC LIMIT 7", args).fetchall()
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

    def create_incident(self, *, entity_id: str, cluster_key: str, level: str,
                        doc_id: str, summary: str, ts: str) -> dict:
        """创建待确认事件。incident_id 含时间，冷却期后的同簇可形成新事件。"""
        incident_id = hashlib.sha1(
            f"{entity_id}:{cluster_key}:{ts}".encode("utf-8")).hexdigest()[:20]
        self.conn.execute(
            "INSERT OR IGNORE INTO incidents(incident_id,entity_id,cluster_key,level,status,doc_id,summary,"
            "created_at,updated_at,actor,note) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (incident_id, entity_id, cluster_key, level, "pending_confirmation", doc_id,
             summary, ts, ts, "system", ""),
        )
        return dict(self.conn.execute(
            "SELECT * FROM incidents WHERE incident_id=?", (incident_id,)).fetchone())

    def get_incident(self, incident_id: str):
        r = self.conn.execute("SELECT * FROM incidents WHERE incident_id=?", (incident_id,)).fetchone()
        return dict(r) if r else None

    def list_incidents(self, status: Optional[str] = None, limit: int = 100) -> list[dict]:
        q, args = "SELECT * FROM incidents", ()
        if status:
            q += " WHERE status=?"
            args = (status,)
        q += " ORDER BY created_at DESC LIMIT ?"
        return [dict(r) for r in self.conn.execute(q, args + (limit,)).fetchall()]

    def transition_incident(self, incident_id: str, status: str, *, actor: str,
                            note: str = "", ts: str = "") -> bool:
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
            "WHERE rv.doc_id IS NULL AND (f.confidence < ? OR f.is_ironic=1 OR f.risk >= ? "
            "OR f.signals LIKE '%cross_disagree%') "
            "ORDER BY f.risk DESC, f.confidence ASC LIMIT ?", (conf_lt, risk_ge, limit)).fetchall()

    def pending_review_count(self, conf_lt: float = 0.6, risk_ge: float = 30.0) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM clean c JOIN features f USING(doc_id) "
            "LEFT JOIN review rv ON rv.doc_id=c.doc_id "
            "WHERE rv.doc_id IS NULL AND (f.confidence < ? OR f.is_ironic=1 OR f.risk >= ? "
            "OR f.signals LIKE '%cross_disagree%')",
            (conf_lt, risk_ge)).fetchone()[0]

    def add_review(self, doc_id: str, verdict: str, note: str = "", ts: str = "", kind: str = "qc",
                   actor: str = "", *, commit: bool = True) -> None:
        """记录人工复核结论（verdict 如 ok/改负/改正/串味/水军/危机确认）。"""
        self.conn.execute(
            "INSERT INTO review(doc_id,kind,verdict,note,ts,actor) VALUES(?,?,?,?,?,?)",
            (doc_id, kind, verdict, note, ts, actor),
        )
        if commit:
            self.conn.commit()

    def review_stats(self) -> dict:
        """质检 KPI：已复核数 + 机器判错数（verdict!=ok）。"""
        r = self.conn.execute(
            "SELECT COUNT(*) n, SUM(CASE WHEN verdict<>'ok' THEN 1 ELSE 0 END) wrong FROM review").fetchone()
        return {"reviewed": r["n"] or 0, "machine_wrong": r["wrong"] or 0}

    # --- Phase A: 多维标注（训练模式地基）---
    def add_annotation(self, doc_id: str, *, subject: Optional[str] = None,
                       stance: Optional[str] = None, importance: Optional[str] = None,
                       picked_words: Optional[list] = None, note: str = "",
                       sample_source: str = "manual", entity_id: Optional[str] = None,
                       ts: str = "", annotator: str = "local") -> int:
        """写一条多维标注。picked_words=[{word,role,span}]。append-only，id 大者为最新。"""
        cur = self.conn.execute(
            "INSERT INTO annotations(doc_id,subject,stance,importance,picked_words,note,"
            "annotator,sample_source,entity_id,ts) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (doc_id, subject, stance, importance, json.dumps(picked_words or [], ensure_ascii=False),
             note, annotator, sample_source, entity_id, ts))
        self.conn.commit()
        return cur.lastrowid

    def annotation_candidates(self, entity_id: Optional[str] = None, limit: int = 200):
        """未标注的机器判定帖（廉价预筛，供主动学习采样器二次打分）。已标即出队。"""
        q = ("SELECT c.doc_id,c.platform,c.author,c.author_followers,c.text,c.url,c.publish_ts,"
             "c.embedding,c.entity_id,f.polarity,f.confidence,f.signals,f.risk "
             "FROM clean c JOIN features f USING(doc_id) "
             "LEFT JOIN annotations a ON a.doc_id=c.doc_id WHERE a.doc_id IS NULL")
        args: tuple = ()
        if entity_id:
            q += (" AND EXISTS(SELECT 1 FROM document_entities de "
                  "WHERE de.doc_id=c.doc_id AND de.entity_id=?)"); args = (entity_id,)
        q += " ORDER BY f.confidence ASC, f.risk DESC LIMIT ?"
        return self.conn.execute(q, args + (limit,)).fetchall()

    def annotated_count(self) -> int:
        return self.conn.execute("SELECT COUNT(DISTINCT doc_id) FROM annotations").fetchone()[0]

    def latest_annotation(self, doc_id: str):
        """某帖最新一条标注（重标回填 / few-shot 范例源）。"""
        return self.conn.execute(
            "SELECT * FROM annotations WHERE doc_id=? ORDER BY id DESC LIMIT 1", (doc_id,)).fetchone()

    def load_annotations(self, entity_id: Optional[str] = None):
        """已标满三维的样本 ⋈ clean（含正文/粉丝/向量），供 Phase C few-shot 范例。同 doc 取最新。"""
        q = ("SELECT a.doc_id, a.subject, a.stance, a.importance, a.picked_words, "
             "c.text, c.author_followers, c.embedding FROM annotations a "
             "JOIN clean c USING(doc_id) WHERE a.subject IS NOT NULL "
             "AND a.id IN (SELECT MAX(id) FROM annotations GROUP BY doc_id)")
        args: tuple = ()
        if entity_id:
            q += (" AND EXISTS(SELECT 1 FROM document_entities de "
                  "WHERE de.doc_id=c.doc_id AND de.entity_id=?)"); args = (entity_id,)
        return self.conn.execute(q, args).fetchall()

    # --- Phase A/⑥: 官方账号白名单（主体维确定性判定）---
    def add_account(self, author: str, subject_type: str, *, platform: str = "",
                    entity_id: Optional[str] = None, note: str = "", ts: str = "") -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO account_registry(platform,author,subject_type,entity_id,note,ts) "
            "VALUES(?,?,?,?,?,?)", (platform, author, subject_type, entity_id, note, ts))
        self.conn.commit()

    def account_type(self, author: str, platform: str = "") -> Optional[str]:
        """查账号主体类型：精确 (platform,author) 优先，回退跨平台 (author)。未登记返回 None。"""
        if not author:
            return None
        r = self.conn.execute(
            "SELECT subject_type FROM account_registry WHERE author=? AND (platform=? OR platform='') "
            "ORDER BY CASE WHEN platform=? THEN 0 ELSE 1 END LIMIT 1",
            (author, platform, platform)).fetchone()
        return r["subject_type"] if r else None

    def list_accounts(self, entity_id: Optional[str] = None):
        q = "SELECT * FROM account_registry"
        args: tuple = ()
        if entity_id:
            q += " WHERE entity_id IS ? OR entity_id IS NULL"; args = (entity_id,)
        return self.conn.execute(q + " ORDER BY subject_type, author", args).fetchall()

    def delete_account(self, account_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM account_registry WHERE id=?", (account_id,))
        self.conn.commit()
        return cur.rowcount > 0

    # --- v1-C: 心跳（无人值守存活探测）---
    def record_heartbeat(self, ts: str, status: str, note: str = "") -> None:
        """每次跑批后记录心跳。last_success 仅在成功时前移，供 deadman 判定'多久没成功了'。"""
        prev = self.conn.execute("SELECT last_success FROM heartbeat WHERE id=1").fetchone()
        last_success = ts if status == "ok" else (prev["last_success"] if prev else "")
        self.conn.execute(
            "INSERT OR REPLACE INTO heartbeat(id,last_start,last_success,last_status,note) "
            "VALUES(1,?,?,?,?)", (ts, last_success, status, note))
        self.conn.commit()

    def get_heartbeat(self) -> Optional[dict]:
        r = self.conn.execute(
            "SELECT last_start,last_success,last_status,note FROM heartbeat WHERE id=1").fetchone()
        return dict(r) if r else None

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.commit()
        self.conn.close()
