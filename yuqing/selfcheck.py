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
    "entities": [{"id": "myproduct", "type": "self", "aliases": ["星海手机"]}],
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

    # 5) 报告：数字来自 metrics、引用全部有效
    md = build_report(store, WATCH, run_id="r1", now="2026-07-06T10:00:00+08:00",
                      health_by_platform=hbp, use_claude=False)
    assert f"| 总声量 | {m['n_total']} |" in md, "报告数字与 metrics 不一致"
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

    print("OK selfcheck —— 整条链跑通：")
    print(f"  去重 clean={n_clean}｜features 全带 evidence 子串｜Top负面={top['native_id']}(risk={top['risk']})")
    print(f"  报告数字与 metrics 一致、引用校验通过、伪造引用被抓")
    print(f"  静默失败三态：{hbp2} → 红条已挂")
    print(f"  只读看板渲染：健康徽章 + 报告历史 + 负面Top 全部就位")
    print("\n--- 生成的周报（happy path，节选）---\n")
    print(md[:900])


if __name__ == "__main__":
    demo()
