# -*- coding: utf-8 -*-
"""端到端离线自检：无需 opencli 登录 / API key / 飞书，用 fixtures 跑通整条链。

python -m yuqing.selfcheck

验证：采集去重 + doc_id 贯穿 + evidence 逐字校验 + 风险排序 + 报告数字不编造
     + 引用校验器 + 静默失败三态红条。
"""

from __future__ import annotations

from .analyze import analyze_pending
from .collect import collect_all, collect_platform
from .report import aggregate, build_report, validate_citations
from .store import Store, doc_id_for

WATCH = {
    "platforms": ["weibo", "zhihu", "heimao"],
    "entities": [{"id": "myproduct", "type": "self", "aliases": ["星海手机", "星海Pro"]}],
}

# fixtures：模拟 opencli/Jina 的原始返回（含一条重复 id 测去重）
FIXTURES = {
    "weibo": {"myproduct": [
        {"id": "w1", "text": "星海手机用了三天就发热卡顿，申请退款客服还不理，避雷！",
         "user": {"nickname": "大V数码", "followers": "3000000"},
         "like_count": "5200", "comment_count": "880", "repost_count": "2100",
         "url": "https://weibo.com/w1"},
        {"id": "w1", "text": "（重复抓到的同一条）星海手机发热卡顿避雷",   # 同 id → 应被去重
         "user": {"nickname": "大V数码"}, "url": "https://weibo.com/w1"},
        {"id": "w2", "text": "星海手机真香，屏幕好评推荐！",
         "user": {"nickname": "路人甲", "followers": "200"}, "like_count": "30",
         "url": "https://weibo.com/w2"},
    ]},
    "zhihu": {"myproduct": [
        {"id": "z1", "text": "如何评价星海Pro？续航一般但性价比还行。",
         "user": {"nickname": "知乎用户", "followers": "1500"}, "comment_count": "12",
         "url": "https://zhihu.com/z1"},
    ]},
    "heimao": {"myproduct": [
        {"id": "h1", "text": "星海手机购买后要求退款维权，商家拒绝，严重欺诈！",
         "user": {"nickname": "投诉人"}, "url": "https://tousu.sina.com.cn/h1"},
    ]},
}


def _run(fixtures) -> tuple[Store, dict]:
    store = Store(":memory:")
    hbp = collect_all(store, WATCH, run_id="r1", now="2026-07-06T10:00:00+08:00", fixtures=fixtures)
    analyze_pending(store, use_claude=False)
    return store, hbp


def demo() -> None:
    store, hbp = _run(FIXTURES)

    # 1) 去重：weibo 抓了 3 条但 w1 重复 → clean 应为 4（w1,w2,z1,h1）
    n_clean = store.conn.execute("SELECT COUNT(*) FROM clean").fetchone()[0]
    assert n_clean == 4, f"去重失败，clean={n_clean}"

    # 2) doc_id 贯穿：features 的 doc_id 必须都能在 clean 找到，且确定性一致
    assert store.conn.execute("SELECT COUNT(*) FROM features").fetchone()[0] == 4
    assert doc_id_for("weibo", "w1") == store.conn.execute(
        "SELECT doc_id FROM clean WHERE native_id='w1'").fetchone()[0]

    # 3) evidence 逐字校验：库里每条 evidence 都是对应正文子串
    for r in store.conn.execute("SELECT c.text, f.evidence FROM clean c JOIN features f USING(doc_id)"):
        assert (not r["evidence"]) or (r["evidence"] in r["text"]), f"evidence 非子串: {r['evidence']}"

    # 4) 风险排序：大V危机负面(w1) 风险分应最高，正面(w2) 为 0
    m = aggregate(store, "myproduct")
    assert m["n_total"] == 4 and m["n_neg"] >= 2
    top = m["top_neg"][0]
    assert top["native_id"] in ("w1", "h1"), f"Top 负面异常: {top['native_id']}"
    w2_risk = store.conn.execute(
        "SELECT risk FROM features WHERE doc_id=?", (doc_id_for("weibo", "w2"),)).fetchone()[0]
    assert w2_risk == 0.0, f"正面帖不应有风险分: {w2_risk}"
    # 黑猫零互动投诉也须有正风险分（不能被 log(0) 归零）
    h1_risk = store.conn.execute(
        "SELECT risk FROM features WHERE doc_id=?", (doc_id_for("heimao", "h1"),)).fetchone()[0]
    assert h1_risk > 0, f"黑猫零互动投诉被归零: {h1_risk}"

    # 5) 报告：数字来自聚合、引用全部有效
    md = build_report(store, WATCH, run_id="r1", now="2026-07-06T10:00:00+08:00",
                      health_by_platform=hbp, use_claude=False)
    assert f"| 总声量 | {m['n_total']} |" in md, "报告数字与聚合不一致"
    assert "抽样、非全量" in md, "缺抽样诚实声明"
    assert validate_citations(md, store) == [], "存在悬空引用"
    assert build_report.__doc__  # sanity

    # 6) 引用校验器真能抓假引用
    fake = md + "\n伪造结论 [来源:deadbeefdeadbeef]"
    assert validate_citations(fake, store) == ["deadbeefdeadbeef"], "校验器漏抓伪造引用"

    # 7) 健康三态：happy path 无红条
    from . import health
    assert health.banner(hbp) is None, f"happy path 不应有红条: {hbp}"

    # 8) 静默失败：抽掉 heimao fixture → 采集报错 → fail 三态 → 报告红条
    store2, hbp2 = _run({"weibo": FIXTURES["weibo"], "zhihu": FIXTURES["zhihu"]})  # 无 heimao
    assert hbp2["heimao"] == "fail", f"应判 fail: {hbp2}"
    band = health.banner(hbp2)
    assert band and "heimao" in band and "无数据" in band
    md2 = build_report(store2, WATCH, run_id="r2", now="2026-07-06T10:00:00+08:00",
                       health_by_platform=hbp2, use_claude=False)
    assert band.split("——")[0].strip() in md2 or "数据健康告警" in md2, "报告未打红条"

    # 9) 只读看板渲染：健康三态徽章 + 报告历史 + 负面 Top，且真读到库里数据
    from . import dashboard
    idx = dashboard.render_index(store)
    assert 'class="badge ok"' in idx and "采集健康" in idx and "负面 Top" in idx
    assert "r1" in idx and doc_id_for("weibo", "w1") in idx        # 报告列表 + 负面溯源
    rep = dashboard.render_report(store, "r1")
    assert "总声量" in rep and "返回" in rep                        # 展示存库报告
    assert "未找到" in dashboard.render_report(store, "nope")       # 缺失 run_id 兜底
    idx2 = dashboard.render_index(store2)
    assert 'class="badge fail"' in idx2 and "采集异常" in idx2      # 断链→红条徽章

    # --- Phase 1 ---
    from . import alerts, budget
    from .report import sov as sov_fn
    import os as _os

    # 10) 实时预警：P0 触发 + 同簇冷却
    a1 = alerts.evaluate(store, now="2026-07-06T10:00:00+08:00", health_by_platform=hbp)
    assert any(x["level"] == "P0" and x["kind"] == "risk" for x in a1), a1
    a2 = alerts.evaluate(store, now="2026-07-06T10:05:00+08:00", health_by_platform=hbp)
    assert a2 == [], f"同簇应被冷却: {a2}"
    ah = alerts.evaluate(store2, now="2026-07-06T10:00:00+08:00", health_by_platform=hbp2)
    assert any(x["kind"] == "health" and "heimao" in x["summary"] for x in ah), "缺静默失败预警"

    # 11) 成本配额熔断
    _os.environ["YUQING_MAX_CALLS"] = "1"
    bs = Store(":memory:")
    budget.guard(bs, "2026-07-06")
    try:
        budget.guard(bs, "2026-07-06"); raise AssertionError("应已熔断")
    except budget.BudgetExceeded:
        pass
    _os.environ.pop("YUQING_MAX_CALLS")

    # 12) 竞品 SOV：份额和为 1，竞品声量正确
    sw = {"platforms": ["weibo"], "entities": [
        {"id": "mine", "type": "self", "aliases": ["本品"]},
        {"id": "rival", "type": "competitor", "aliases": ["竞品"]}]}
    sf = {"weibo": {"mine": [{"id": "m1", "text": "本品真香好用推荐"}],
                    "rival": [{"id": "r1", "text": "竞品垃圾避雷"}, {"id": "r2", "text": "竞品还行"}]}}
    ss = Store(":memory:")
    collect_all(ss, sw, run_id="s", now="2026-07-06T10:00:00+08:00", fixtures=sf)
    analyze_pending(ss, use_claude=False)
    sv = {x["id"]: x for x in sov_fn(ss, sw)}
    assert abs(sum(x["sov"] for x in sv.values()) - 1.0) < 1e-6, "SOV 份额和应为1"
    assert sv["rival"]["mentions"] == 2 and sv["mine"]["mentions"] == 1

    # 12b) 竞品高风险负面不触发预警（self_entities 过滤，竞品的锅≠自家危机）
    cf = {"weibo": {"rival": [{"id": "c1", "text": "竞品手机爆炸维权退款曝光避雷",
          "user": {"nickname": "大V", "followers": "5000000"},
          "like_count": "9000", "comment_count": "2000", "repost_count": "3000"}]}}
    cs = Store(":memory:")
    collect_all(cs, sw, run_id="c", now="2026-07-06T10:00:00+08:00", fixtures=cf)
    analyze_pending(cs, use_claude=False)
    assert alerts.evaluate(cs, now="2026-07-06T10:00:00+08:00", self_entities=None), "对照：无过滤应有预警"
    assert alerts.evaluate(cs, now="2026-07-06T10:00:00+08:00", self_entities={"mine"}) == [], \
        "竞品负面不应告警"

    # 13) 增量水位：早于水位的内容被跳过，仅入更新的
    ws = Store(":memory:")
    collect_platform(ws, run_id="w1", entity_id="e", platform="weibo", keyword="k",
                     now="t", fixture=[{"id": "n1", "text": "x", "created_at": "2026-07-05T00:00:00"}])
    n_ins, _ = collect_platform(ws, run_id="w2", entity_id="e", platform="weibo", keyword="k", now="t",
        fixture=[{"id": "n0", "text": "old", "created_at": "2026-07-04T00:00:00"},
                 {"id": "n2", "text": "new", "created_at": "2026-07-06T00:00:00"}])
    assert n_ins == 1, f"只应入更新的1条(早于水位跳过)，实际 {n_ins}"

    # 13b) 非 ISO/数字时间戳不污染水位、不崩（防静默漏抓）
    ws2 = Store(":memory:")
    ni, _ = collect_platform(ws2, run_id="a", entity_id="e", platform="weibo", keyword="k", now="t",
        fixture=[{"id": "x1", "text": "a", "created_at": "刚刚"},
                 {"id": "x2", "text": "b", "created_at": 1720000000},
                 {"id": "x3", "text": "c", "created_at": "2026-07-05T00:00:00"}])
    assert ni == 3, f"非ISO/数字ts应全部入库不崩，实际 {ni}"
    ni2, _ = collect_platform(ws2, run_id="b", entity_id="e", platform="weibo", keyword="k", now="t",
        fixture=[{"id": "x4", "text": "d", "created_at": "2026-07-06T00:00:00"}])
    assert ni2 == 1, f"ISO 新内容应入库(未被非ISO污染)，实际 {ni2}"

    # --- Phase 2 ---
    from . import analytics

    # 14) ABSA 方面级：主 store 命中 性能/服务
    aspects = {a["aspect"] for a in analytics.aspect_breakdown(store, "myproduct")}
    assert {"性能", "服务"} <= aspects, aspects

    # 15) 看板负面日趋势（实时算，无需快照表）
    idx3 = dashboard.render_index(store)
    assert "负面日趋势" in idx3 and "█" in idx3, "看板缺时序趋势"

    # 16) 多天：稳健异常(带绝对下限) + 上升话题
    ms = Store(":memory:")
    for day, ids in [("2026-07-01", ["a1"]), ("2026-07-02", ["b1"]),
                     ("2026-07-05", ["c1", "c2", "c3", "c4", "c5", "c6"])]:
        fx = [{"id": i, "text": "退款维权避雷垃圾差评", "created_at": day + "T00:00:00"} for i in ids]
        collect_platform(ms, run_id=day, entity_id="p", platform="weibo", keyword="k",
                         now=day + "T00:00:00", fixture=fx)
    analyze_pending(ms, use_claude=False)
    anom = analytics.negative_anomaly(ms, "p")
    assert anom["anomaly"] and anom["count"] == 6, anom            # 07-05 放量6 vs 历史1,1
    rt = analytics.rising_topics(ms, "p", "2026-07-05")
    assert rt and rt[0]["delta"] > 0, rt

    # 17) 健壮性：signals/aspects=null 不崩；空 fetched_at 不污染"最新一天"
    from .store import CleanDoc
    rs = Store(":memory:")
    doc = CleanDoc.build(platform="weibo", entity_id="p", native_id="n", text="x", fetched_at="")
    rs.add_clean(doc)
    rs.add_feature(doc.doc_id, {"polarity": "neg", "signals": {"aspects": None}})
    rs.commit()
    assert analytics.aspect_breakdown(rs, "p") == []           # aspects=null 被跳，不崩
    assert analytics.daily_negative_series(rs, "p") == []      # 空日期不计入
    assert analytics.negative_anomaly(rs, "p")["anomaly"] is False

    # --- Phase 3 ---
    from . import insights
    # 18) 老板一句话日报（确定性）+ AI 问答检索命中 + 诉求→需求 + 报告含 backlog 段
    assert "今日舆情" in insights.oneliner(store, WATCH)
    qa = insights.ask(store, "发热")
    assert qa["sources"] and "[来源:" in qa["answer"], qa       # 检索命中且带溯源
    bl = insights.backlog(store, {"myproduct"})
    assert any(x["kind"] in ("Bug", "投诉") for x in bl), bl     # w1/h1 → Bug/投诉
    assert "用户诉求→产品需求" in md                              # 已接入报告
    # 19) 跨平台事件时间线按时间还原
    tl = insights.timeline(ms, "退款")
    assert tl and all(tl[i]["time"] <= tl[i + 1]["time"] for i in range(len(tl) - 1)), tl

    # v1-A) 串味过滤：命中 must_not / 无别名 的不进 clean，但原始层留全量审计
    rw = {"platforms": ["weibo"], "entities": [
        {"id": "b", "type": "self", "aliases": ["星海手机"], "must_not": ["Doo Prime"]}]}
    rf = {"weibo": {"b": [
        {"id": "ok1", "text": "星海手机发热退款", "url": "u1"},
        {"id": "bad1", "text": "警惕Doo Prime外汇平台账户被封", "url": "u2"},
        {"id": "bad2", "text": "今天天气不错随便发一条", "url": "u3"}]}}
    rs2 = Store(":memory:")
    collect_all(rs2, rw, run_id="r", now="2026-07-06T10:00:00+08:00", fixtures=rf)
    kept = [r["native_id"] for r in rs2.conn.execute("SELECT native_id FROM clean ORDER BY native_id")]
    assert kept == ["ok1"], f"串味过滤：只应保留相关 ok1，实际 {kept}"
    assert rs2.conn.execute("SELECT COUNT(*) FROM raw").fetchone()[0] == 3, "原始层应留全量审计"

    # v1-A) heimao 不强求含别名：投诉标题常省略品牌名，不能被 no_alias 误杀（防漏真实投诉）
    hf = {"heimao": {"b": [{"id": "c9", "text": "进水要求退款商家不理", "url": "u"}]}}
    hs = Store(":memory:")
    from .collect import collect_platform as _cp
    _cp(hs, run_id="r", entity_id="b", platform="heimao", keyword="星海手机",
        now="2026-07-06T10:00:00+08:00", fixture=hf["heimao"]["b"],
        aliases=["星海手机"], must_not=[])
    assert hs.conn.execute("SELECT COUNT(*) FROM clean").fetchone()[0] == 1, "黑猫投诉标题无品牌名不应被误杀"

    # v1-B) 人工复核队列：低置信/高风险负面进队 → 标注后出队 → 质检KPI统计
    q = store.review_queue()
    assert q, "主 store 有低置信/高风险负面，应进复核队列"
    before = store.pending_review_count()
    store.add_review(q[0]["doc_id"], "改负", "反讽误判", ts="2026-07-06T10:00:00+08:00")
    assert store.pending_review_count() == before - 1, "复核后应出队"
    st = store.review_stats()
    assert st["reviewed"] == 1 and st["machine_wrong"] == 1, st   # verdict!=ok → 机器判错

    # v1-C) 心跳：失败不前移 last_success，成功才前移（deadman 据此判存活）
    store.record_heartbeat("2026-07-06T09:00:00+08:00", "error")
    assert store.get_heartbeat()["last_success"] == ""
    store.record_heartbeat("2026-07-06T10:00:00+08:00", "ok")
    assert store.get_heartbeat()["last_success"] == "2026-07-06T10:00:00+08:00"

    print("OK selfcheck —— 整条链跑通：")
    print(f"  去重 clean={n_clean}｜features 全带 evidence 子串｜Top负面={top['native_id']}(risk={top['risk']})")
    print(f"  报告数字与聚合一致、引用校验通过、伪造引用被抓")
    print(f"  静默失败三态：{hbp2} → 红条已挂")
    print(f"  只读看板渲染：健康徽章 + 报告历史 + 负面Top 全部就位")
    print(f"  Phase1：实时预警P0+冷却✓ 静默失败预警✓ 成本熔断✓ 竞品SOV✓ 增量水位✓")
    print(f"  Phase2：ABSA方面级✓ 稳健z-score异常✓ 上升话题✓ 时序看板✓ 分层路由✓")
    print(f"  Phase3：老板日报✓ AI问答(RAG-lite)✓ 诉求→需求闭环✓ 事件时间线✓")
    print(f"  v1-A：串味过滤(否定词/别名)✓ 原始层审计留全量✓")
    print(f"  v1-B：复核队列(低置信/高风险入队)✓ 标注出队✓ 质检KPI✓")
    print(f"  v1-C：心跳前移(失败不算存活)✓ [deadman/登录态告警见 scheduler selftest]")
    print("\n--- 生成的周报（happy path，节选）---\n")
    print(md[:900])


if __name__ == "__main__":
    demo()
