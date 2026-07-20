# -*- coding: utf-8 -*-
"""Legacy tactical and executive analytics views."""

from __future__ import annotations

import html
import json

from ..store import Store
from .common import _STATE_CN, _page, _safe_href
from .compat import load_watch


def chart_data(store: Store, entity_id: str, watch: dict | None = None) -> dict:
    """图表页数据(纯JSON,供 Chart.js fetch)：情绪/声量趋势、方面口碑、话题、SOV、BHI趋势。"""
    from .. import analytics
    from ..report import aggregate, sov as sov_fn
    ser = analytics.daily_series(store, entity_id)
    ab = analytics.aspect_breakdown(store, entity_id)
    m = aggregate(store, entity_id)
    bh = analytics.brand_health(store, entity_id)
    data = {
        "days": [d["day"] for d in ser],
        "sentiment": {"pos": [d["pos"] for d in ser], "neg": [d["neg"] for d in ser],
                      "neu": [d["neu"] for d in ser]},
        "mention": [d["mention"] for d in ser],
        "bhi_trend": analytics.bhi_trend(store, entity_id),
        "aspects": [{"aspect": a["aspect"], "neg_ratio": round(a["neg_ratio"], 3), "n": a["n"]} for a in ab],
        "topics": [{"topic": t, "count": c} for t, c in m["top_topics"]],
        "semantic_topics": [{"size": t["size"], "sample": t["sample"], "platforms": t["platforms"]}
                            for t in analytics.semantic_topics(store, entity_id)[:8]],
        "bhi": bh.get("bhi"), "label": bh.get("label"),
        "platform": [{"platform": p, "total": v["total"], "neg": v["neg"]}
                     for p, v in m["by_platform"].items()],
    }
    if watch and any(e.get("type") == "competitor" for e in watch.get("entities", [])):
        data["sov"] = [{"name": r["name"], "mentions": r["mentions"], "type": r["type"]}
                       for r in sov_fn(store, watch)]
    data["kol"] = [{"author": k["author"], "platform": k["platform"], "followers": k["followers"],
                    "posts": k["posts"], "stance": k["stance"], "mention": k["mention"],
                    "url": k["url"], "sample": k["sample"]}
                   for k in analytics.kol_ranking(store, entity_id, limit=10)]
    data["clusters"] = analytics.suspicious_clusters(store, entity_id)
    return data


def _self_entities(watch: dict) -> list[tuple[str, str]]:
    return [(e["id"], (e.get("aliases") or [e["id"]])[0])
            for e in watch.get("entities", []) if e.get("type", "self") == "self"]


def render_dash(store: Store, entity_id: str, watch: dict | None = None) -> str:
    """中层战情室：Chart.js 渲染 情绪趋势/声量/方面雷达/话题/SOV。数据走 /chart-data 端点。"""
    if watch is None:
        try:
            watch = load_watch()
        except SystemExit:
            watch = {"platforms": [], "entities": []}
    selfs = _self_entities(watch)
    valid_ids = {eid for eid, _ in selfs} | {e["id"] for e in watch.get("entities", [])}
    if entity_id not in valid_ids:
        entity_id = selfs[0][0] if selfs else ""
    tabs = " ".join(f"<a href='/dash?entity={html.escape(eid)}'>"
                    f"{'<b>' if eid == entity_id else ''}{html.escape(nm)}{'</b>' if eid == entity_id else ''}</a>"
                    for eid, nm in selfs)
    eid_js = json.dumps(entity_id).replace("</", "<\\/")
    from .. import analytics
    kols = analytics.kol_ranking(store, entity_id, limit=10)
    kol_rows = "".join(
        f"<tr><td>{html.escape(k['author'])}</td><td>{html.escape(k['platform'])}</td>"
        f"<td>{k['followers'] or '—'}</td><td>{k['posts']}</td>"
        f"<td>{k['stance']}</td><td>{k['mention']}</td>"
        f"<td><a href='{_safe_href(k['url'])}' target=_blank rel=noopener>{html.escape(k['sample'])}</a></td></tr>"
        for k in kols) or "<tr><td colspan=7 class=muted>暂无</td></tr>"
    clus = analytics.suspicious_clusters(store, entity_id)
    clus_rows = "".join(
        f"<tr><td>{c['n_authors']}</td><td>{c['n_docs']}</td><td>{html.escape('、'.join(c['platforms']))}</td>"
        f"<td>{html.escape(c['sample'])}</td></tr>" for c in clus) or \
        "<tr><td colspan=4 class=muted>未发现同质化账号簇</td></tr>"
    body = f"""<h1>战情室看板 <a href='/' style='font-size:14px'>← 详情</a> <a href='/exec' style='font-size:14px'>高管概览</a></h1>
<p>监控对象：{tabs or '(watch.yaml 无自有实体)'}</p>
<div style='display:grid;grid-template-columns:1fr 1fr;gap:20px'>
  <div><h2>情绪趋势</h2><canvas id=c_sent height=180></canvas></div>
  <div><h2>声量当量趋势</h2><canvas id=c_ment height=180></canvas></div>
  <div><h2>品牌健康指数 BHI 趋势</h2><canvas id=c_bhi height=180></canvas></div>
  <div><h2>方面口碑（负面占比）</h2><canvas id=c_asp height=180></canvas></div>
  <div><h2>负面话题分布</h2><canvas id=c_top height=180></canvas></div>
  <div><h2>竞品声量份额</h2><canvas id=c_sov height=180></canvas></div>
</div>
<p class=muted>数据：公开渠道抽样，按发布日聚合。声量当量=跨平台可比声量(平台权重×影响力)。</p>
<h2>KOL / 影响力榜（按声量当量）</h2>
<table><tr><th>作者</th><th>平台</th><th>粉丝</th><th>发帖</th><th>立场</th><th>声量当量</th><th>代表内容</th></tr>{kol_rows}</table>
<h2>疑似异常账号簇（同质文案跨多账号）</h2>
<table><tr><th>账号数</th><th>帖数</th><th>平台</th><th>样例</th></tr>{clus_rows}</table>
<p class=muted>异常簇=同一内容被多个不同账号发布，疑似水军/搬运/控评，仅作嫌疑提示需人工核实。</p>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<script>
fetch('/chart-data?entity='+encodeURIComponent({eid_js})).then(r=>r.json()).then(d=>{{
  const mk=(id,cfg)=>{{const el=document.getElementById(id);if(el&&d.days!==undefined)new Chart(el,cfg);}};
  const NEG='#cf4b2b',POS='#1a7f6b',NEU='#9aa0a6',BLUE='#2f6fd0';
  mk('c_sent',{{type:'line',data:{{labels:d.days,datasets:[
    {{label:'正',data:d.sentiment.pos,borderColor:POS,tension:.3}},
    {{label:'负',data:d.sentiment.neg,borderColor:NEG,tension:.3}},
    {{label:'中',data:d.sentiment.neu,borderColor:NEU,tension:.3}}]}}}});
  mk('c_ment',{{type:'line',data:{{labels:d.days,datasets:[{{label:'声量当量',data:d.mention,borderColor:BLUE,fill:true,backgroundColor:'rgba(47,111,208,.1)',tension:.3}}]}}}});
  mk('c_bhi',{{type:'line',data:{{labels:(d.bhi_trend||[]).map(x=>x.day),datasets:[{{label:'BHI',data:(d.bhi_trend||[]).map(x=>x.bhi),borderColor:'#7048e8',tension:.3}}]}},options:{{scales:{{y:{{min:0,max:100}}}}}}}});
  if(d.aspects&&d.aspects.length)mk('c_asp',{{type:'radar',data:{{labels:d.aspects.map(a=>a.aspect),datasets:[{{label:'负面占比',data:d.aspects.map(a=>a.neg_ratio),borderColor:NEG,backgroundColor:'rgba(207,75,43,.2)'}}]}},options:{{scales:{{r:{{min:0,max:1}}}}}}}});
  if(d.topics&&d.topics.length)mk('c_top',{{type:'bar',data:{{labels:d.topics.map(t=>t.topic),datasets:[{{label:'负面条数',data:d.topics.map(t=>t.count),backgroundColor:NEG}}]}}}});
  if(d.sov&&d.sov.length)mk('c_sov',{{type:'doughnut',data:{{labels:d.sov.map(s=>s.name),datasets:[{{data:d.sov.map(s=>s.mentions),backgroundColor:['#2f6fd0','#cf4b2b','#9a6700','#1a7f6b','#7048e8']}}]}}}});
}});
</script>"""
    return _page("战情室看板", body)


def render_exec(store: Store, watch: dict | None = None) -> str:
    """高管一屏概览：BHI 大数字 · 数据健康灯 · 关键结论 · 最该看的一件事 · 竞品SOV。"""
    from .. import analytics
    from ..report import sov as sov_fn, aggregate
    if watch is None:
        try:
            watch = load_watch()
        except SystemExit:
            watch = {"platforms": [], "entities": []}
    ents = watch.get("entities", [])
    latest: dict[str, str] = {}
    for r in store.conn.execute("SELECT platform, health FROM run_log ORDER BY ts DESC"):
        latest.setdefault(r["platform"], r["health"])
    lamp = ("#cf222e", "危机/采集异常") if any(v == "fail" for v in latest.values()) else \
           ("#9a6700", "存疑") if any(v != "ok" for v in latest.values()) else ("#1a7f37", "正常")
    p = [f"<h1>高管一屏概览 <a href='/' style='font-size:14px'>← 详情看板</a></h1>",
         f"<p>数据健康灯：<b style='color:{lamp[0]}'>● {lamp[1]}</b> "
         f"<span class=muted>（{'、'.join(f'{k}:{_STATE_CN.get(v, v)}' for k, v in sorted(latest.items())) or '暂无采集'}）</span></p>"]
    _col = {"健康": "#1a7f37", "关注": "#9a6700", "预警": "#bc4c00", "危机": "#cf222e"}
    for e in [x for x in ents if x.get("type", "self") == "self"]:
        name = (e.get("aliases") or [e["id"]])[0]
        bh = analytics.brand_health(store, e["id"])
        p.append(f"<h2>{html.escape(name)}</h2>")
        if bh["bhi"] is None:
            p.append("<p class=muted>暂无数据</p>")
            continue
        c = bh["components"]
        p.append(f"<div style='display:flex;align-items:center;gap:24px;margin:8px 0'>"
                 f"<div style='font-size:56px;font-weight:800;color:{_col[bh['label']]};line-height:1'>{bh['bhi']}"
                 f"<span style='font-size:18px;color:#656d76'> /100</span></div>"
                 f"<div><b style='color:{_col[bh['label']]};font-size:18px'>{bh['label']}</b><br>"
                 f"<span class=muted>情绪{c['sentiment']} · 声量{c['volume']} · 危机{c['crisis']} · 方面{c['aspect']}</span></div></div>")
        m = aggregate(store, e["id"])
        concl = [f"本期声量 {m['n_total']} 条，负面 {m['n_neg']}（{m['neg_ratio']:.0%}）"]
        if m["top_topics"]:
            concl.append(f"最需关注话题：「{m['top_topics'][0][0]}」（{m['top_topics'][0][1]} 条负面）")
        if bh["crisis_neg"]:
            concl.append(f"⚠️ 有 {bh['crisis_neg']} 条命中危机词，需人工核实")
        p.append("<b>关键结论</b><ul>" + "".join(f"<li>{html.escape(x)}</li>" for x in concl) + "</ul>")
        if m["top_neg"]:
            t = m["top_neg"][0]
            p.append(f"<p><b>最该看的一件事：</b>[{html.escape(t['platform'])}] 风险{t['risk']} · "
                     f"<a href='{_safe_href(t['url'])}' target=_blank rel=noopener>{html.escape((t['summary'] or '')[:44])}</a></p>")
    if any(x.get("type") == "competitor" for x in ents):
        rows = sov_fn(store, watch)
        p.append("<h2>竞品声量对标（SOV）</h2><table><tr><th>对象</th><th>类型</th><th>声量</th><th>份额</th><th>净情绪</th></tr>"
                 + "".join(f"<tr><td>{html.escape(r['name'])}</td><td>{'自有' if r['type']=='self' else '竞品'}</td>"
                          f"<td>{r['mentions']}</td><td>{r['sov']:.0%}</td><td>{r['nsr']:+.2f}</td></tr>" for r in rows)
                 + "</table>")
    p.append("<p class=muted>BHI=品牌健康指数(0-100,均衡型:情绪/声量/危机/方面)。公开渠道抽样，仅供趋势参考。</p>")
    return _page("高管一屏概览", "".join(p))
