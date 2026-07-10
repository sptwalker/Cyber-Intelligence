# -*- coding: utf-8 -*-
"""核心架构回归：多实体归属、观测快照、alias 搜索、分析血缘、告警确认门。"""

from __future__ import annotations

import os
import sqlite3
import tempfile

from . import alerts, collect
from .analyze import ANALYSIS_VERSION, analyze_pending
from .report import sov
from .store import SCHEMA_VERSION, Store


def main() -> int:
    # 旧库升级：补列、新表、实体关系回填均应自动完成。
    old_path = tempfile.mktemp(suffix=".db")
    old = sqlite3.connect(old_path)
    old.executescript("""
        CREATE TABLE clean (
            doc_id TEXT PRIMARY KEY, platform TEXT, native_id TEXT, entity_id TEXT,
            author TEXT, author_followers INTEGER, text TEXT, likes INTEGER, comments INTEGER,
            reposts INTEGER, publish_ts TEXT, url TEXT, tags TEXT, content_cluster TEXT,
            is_complaint INTEGER, backend TEXT, fetched_at TEXT
        );
        CREATE TABLE features (
            doc_id TEXT PRIMARY KEY, polarity TEXT, intensity REAL, confidence REAL,
            is_ironic INTEGER, is_spam INTEGER, topic_label TEXT, summary TEXT,
            evidence TEXT, signals TEXT, risk REAL
        );
        INSERT INTO clean VALUES(
            'legacy-doc','weibo','legacy-1','legacy-brand','',0,'旧数据',0,0,0,'','','','',0,'','2026-07-01');
    """)
    old.commit(); old.close()
    migrated = Store(old_path)
    try:
        assert migrated.schema_version() == SCHEMA_VERSION
        assert migrated.conn.execute(
            "SELECT entity_id FROM document_entities WHERE doc_id='legacy-doc'").fetchone()[0] == "legacy-brand"
        cols = {r[1] for r in migrated.conn.execute("PRAGMA table_info(features)")}
        assert {"analysis_version", "engine", "model", "prompt_version", "analyzed_at"} <= cols
    finally:
        migrated.close()
        os.remove(old_path)

    store = Store(":memory:")
    calls: list[str] = []
    original_fetch = collect._fetch_opencli

    def fake_fetch(platform: str, keyword: str, limit: int):
        calls.append(keyword)
        return [{
            "id": "shared-1",
            "text": "Alpha盒子和Beta盒子对比：Alpha发热卡顿，申请退款",
            "like_count": 10,
            "url": "https://example.test/shared-1",
        }]

    watch = {
        "platforms": ["weibo"],
        "entities": [
            {"id": "brand_a", "type": "self", "aliases": ["Alpha", "Alpha盒子"]},
            {"id": "brand_b", "type": "competitor", "aliases": ["Beta"]},
        ],
    }
    try:
        collect._fetch_opencli = fake_fetch
        collect.collect_all(store, watch, run_id="r1", now="2026-07-10T10:00:00+08:00")
    finally:
        collect._fetch_opencli = original_fetch

    assert calls == ["Alpha", "Alpha盒子", "Beta"], calls
    assert store.conn.execute("SELECT COUNT(*) FROM clean").fetchone()[0] == 1
    relations = store.conn.execute(
        "SELECT entity_id FROM document_entities ORDER BY entity_id").fetchall()
    assert [r[0] for r in relations] == ["brand_a", "brand_b"], relations
    assert store.conn.execute("SELECT COUNT(*) FROM raw_observations").fetchone()[0] == 3
    assert store.conn.execute("SELECT COUNT(*) FROM engagement_snapshots").fetchone()[0] == 1

    # 第二轮刷新互动：稳定文档不重复，但原始观测/互动快照追加，当前值前移。
    collect.collect_platform(
        store, run_id="r2", entity_id="brand_a", platform="weibo", keyword="Alpha",
        now="2026-07-10T11:00:00+08:00", entry="search:Alpha", aliases=["Alpha", "Alpha盒子"],
        fixture=[{"id": "shared-1", "text": "Alpha盒子和Beta盒子对比", "like_count": 99}],
    )
    assert store.conn.execute("SELECT likes FROM clean").fetchone()[0] == 99
    assert store.conn.execute("SELECT COUNT(*) FROM engagement_snapshots").fetchone()[0] == 2
    assert store.conn.execute("SELECT COUNT(*) FROM raw_observations").fetchone()[0] == 4

    # 分析结果带版本和历史记录；多实体 SOV 不受首次归属影响。
    analyze_pending(store, use_claude=False, now="2026-07-10T11:01:00+08:00")
    feature = store.conn.execute(
        "SELECT analysis_version,engine,analyzed_at FROM features").fetchone()
    assert feature["analysis_version"] == ANALYSIS_VERSION and feature["engine"] == "rule"
    assert store.conn.execute("SELECT COUNT(*) FROM analysis_results").fetchone()[0] == 1
    shares = {x["id"]: x for x in sov(store, watch)}
    assert shares["brand_a"]["mentions"] == 1 and shares["brand_b"]["mentions"] == 1, shares

    # 同一内容对两个实体各自形成事件，不再因全局 content hash 相互冷却。
    doc_id = store.conn.execute("SELECT doc_id FROM clean").fetchone()[0]
    store.add_feature(
        doc_id, {"polarity": "neg", "intensity": 1.0, "confidence": 0.9,
                 "summary": "发热退款危机", "signals": {"crisis": True}, "risk": 150.0},
        analysis_version=ANALYSIS_VERSION, engine="rule", model="test",
        prompt_version="test", analyzed_at="2026-07-10T11:02:00+08:00")
    pending = alerts.evaluate(
        store, now="2026-07-10T11:03:00+08:00", self_entities={"brand_a", "brand_b"})
    assert len(pending) == 2 and all(a["status"] == "pending_confirmation" for a in pending), pending
    incident_id = pending[0]["incident_id"]
    sent: list[str] = []
    original_push = alerts.push_feishu_alert_card
    alerts.push_feishu_alert_card = lambda *a, **k: (sent.append("sent"), True)[1]
    blocked = alerts.transition(
        store, incident_id, "escalate", actor="tester", now="2026-07-10T11:03:30+08:00",
        executive_webhook="mock")
    assert not blocked["success"] and not sent, blocked  # 未确认绝不能先推高层
    alerts.push_feishu_alert_card = lambda *a, **k: False  # confirm 只落 confirmed，不触网
    confirmed = alerts.transition(
        store, incident_id, "confirm", actor="tester", now="2026-07-10T11:04:00+08:00",
        executive_webhook="mock")
    assert confirmed["success"] and confirmed["incident"]["status"] == "confirmed", confirmed
    resolved = alerts.transition(
        store, incident_id, "resolve", actor="tester", now="2026-07-10T11:05:00+08:00")
    assert resolved["success"] and resolved["incident"]["status"] == "resolved", resolved
    alerts.push_feishu_alert_card = original_push

    print("OK architecture: 旧库迁移✓ 多alias真实搜索✓ 多实体归属✓ 原始观测/互动快照✓ 分析版本✓ 告警确认门✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
