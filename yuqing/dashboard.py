# -*- coding: utf-8 -*-
"""最小看板：报告历史 + 采集健康三态 + 负面 Top + 系统配置页。

ponytail: 数据视图只读 → stdlib http.server 直读 SQLite，零新依赖。/config 是唯一写入口
（表单存 yuqing_config.json），仅限本机（127.0.0.1 绑定 + Host 头校验）。
    python -m yuqing.dashboard yuqing.db      # 起服务，浏览器开 http://127.0.0.1:8000
"""

from __future__ import annotations

import html
import json
import re
import threading
import io
import contextlib
import datetime as _dt
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from .store import Store

# 跑批触发状态（单机、内存态）。后台线程跑 run.main，主线程照常服务/api/run/status。
# ponytail: GIL 保护的 dict + 一把锁守住"是否在跑"的读改；单机看板无需任务队列。
_run_lock = threading.Lock()
_run_state = {"running": False, "last": None, "current": "", "stop": False}
#   current: 实时进度文字（"正在采集微博数据…"）；stop: 协作式中止标志

_PLATFORM_CN = {"weibo": "微博", "zhihu": "知乎", "xiaohongshu": "小红书", "douyin": "抖音",
                "bilibili": "B站", "tieba": "贴吧", "hupu": "虎扑", "smzdm": "值得买",
                "weixin": "公众号", "heimao": "黑猫投诉"}
_STAGE_CN = {"_analyze": "正在分析情感/方面…", "_embed": "正在语义向量化…", "_report": "正在生成报告…"}


def _progress(entity_id, platform) -> None:
    if platform in _STAGE_CN:
        _run_state["current"] = _STAGE_CN[platform]
    else:
        _run_state["current"] = f"正在采集{_PLATFORM_CN.get(platform, platform)}数据…"


def _do_run(db: str) -> None:
    buf = io.StringIO()
    ok, msg = False, ""
    _run_state["current"] = "正在启动…"
    try:
        from .run import main as run_main
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            code = run_main(db=db, on_progress=_progress, should_stop=lambda: _run_state["stop"])
        stopped = _run_state["stop"]
        ok = (code == 0) and not stopped
        out = buf.getvalue().strip()
        tail = out.splitlines()[-1] if out else f"退出码 {code}"
        msg = ("已停止（部分数据已入库）｜" + tail) if stopped else tail
    except SystemExit as e:
        msg = f"配置/依赖错误：{e}"
    except Exception as e:
        msg = f"运行异常：{str(e)[:200]}"
    finally:
        _run_state["last"] = {"ok": ok, "msg": msg, "at": _dt.datetime.now().strftime("%H:%M:%S")}
        _run_state["current"] = ""
        _run_state["stop"] = False
        _run_state["running"] = False

_CSS = """
body{font:14px/1.5 -apple-system,Segoe UI,Microsoft YaHei,sans-serif;max-width:1000px;margin:24px auto;padding:0 16px;color:#1f2328}
h1{font-size:20px} h2{font-size:16px;margin-top:28px;border-bottom:1px solid #d0d7de;padding-bottom:4px}
table{border-collapse:collapse;width:100%;margin:8px 0} th,td{border:1px solid #d0d7de;padding:6px 8px;text-align:left;vertical-align:top}
th{background:#f6f8fa} a{color:#0969da;text-decoration:none} a:hover{text-decoration:underline}
.badge{padding:1px 8px;border-radius:10px;color:#fff;font-size:12px;white-space:nowrap}
.ok{background:#1a7f37} .suspect{background:#9a6700} .fail{background:#cf222e}
.muted{color:#656d76;font-size:12px} pre{white-space:pre-wrap;background:#f6f8fa;padding:12px;border-radius:6px;overflow:auto}
.spin{display:inline-block;width:14px;height:14px;border:2px solid #d0d7de;border-top-color:#0969da;border-radius:50%;animation:spin .8s linear infinite;vertical-align:-2px;margin-right:6px}
@keyframes spin{to{transform:rotate(360deg)}}
"""

_STATE_CN = {"ok": "正常", "suspect": "存疑", "fail": "失败"}


def _safe_href(url: str) -> str:
    """href 双重防护：先限 http(s)/相对(挡 javascript:)，再 html.escape(挡引号闭合属性突破 XSS)。

    抓来的帖子 URL 不可信——只 scheme-check 挡不住 http://x/'><img onerror=...> 这种属性突破。
    """
    u = (url or "").strip()
    if not u.startswith(("http://", "https://", "/")):
        return "#"
    return html.escape(u, quote=True)


def _badge(state: str) -> str:
    cls = state if state in ("ok", "suspect", "fail") else "muted"
    return f'<span class="badge {cls}">{_STATE_CN.get(state, state)}</span>'


def _page(title: str, body: str) -> str:
    return (f"<!doctype html><html lang=zh><head><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{html.escape(title)}</title><style>{_CSS}</style></head><body>{body}</body></html>")


def render_index(store: Store) -> str:
    conn = store.conn

    # 1) 采集健康：每个平台取最近一次运行状态
    latest: dict[str, dict] = {}
    for r in conn.execute("SELECT platform,health,status,n_fetched,ts,note FROM run_log ORDER BY ts DESC"):
        latest.setdefault(r["platform"], dict(r))
    health_rows = "".join(
        f"<tr><td>{html.escape(p)}</td><td>{_badge(v['health'])}</td>"
        f"<td>{v['n_fetched']}</td><td class=muted>{html.escape(v['ts'])}</td>"
        f"<td class=muted>{html.escape(v['note'] or '')}</td></tr>"
        for p, v in sorted(latest.items())
    ) or "<tr><td colspan=5 class=muted>暂无采集记录</td></tr>"
    any_bad = any(v["health"] != "ok" for v in latest.values())
    banner = ("<p style='color:#cf222e;font-weight:600'>⚠️ 有平台采集异常，下方报告数据可能不全，请人工核查。</p>"
              if any_bad else "")
    pending = store.pending_review_count()
    review_line = (f"<p class=muted>📋 待人工复核 <b>{pending}</b> 条"
                   f"（<code>python -m yuqing.cli review</code>）</p>" if pending else "")

    # 2) 负面日趋势（按 fetched_at 天，实时算，text bar）
    trend = conn.execute(
        "SELECT substr(c.fetched_at,1,10) day, "
        "SUM(CASE WHEN f.polarity='neg' THEN 1 ELSE 0 END) neg, COUNT(*) total "
        "FROM clean c JOIN features f USING(doc_id) GROUP BY day ORDER BY day"
    ).fetchall()
    peak = max((r["neg"] for r in trend), default=0) or 1
    trend_rows = "".join(
        f"<tr><td class=muted>{html.escape(r['day'] or '')}</td><td>{r['neg']}</td>"
        f"<td>{r['total']}</td><td>{'█' * round(20 * r['neg'] / peak)}</td></tr>"
        for r in trend) or "<tr><td colspan=4 class=muted>暂无数据</td></tr>"

    # 3) 报告历史
    report_rows = "".join(
        f"<tr><td><a href='/report?run_id={html.escape(r['run_id'])}'>{html.escape(r['run_id'])}</a></td>"
        f"<td class=muted>{html.escape(r['created_at'])}</td></tr>"
        for r in conn.execute("SELECT run_id,created_at FROM reports ORDER BY created_at DESC LIMIT 50")
    ) or "<tr><td colspan=2 class=muted>暂无报告</td></tr>"

    # 4) 负面 Top（跨全部实体，按风险分）
    neg_rows = "".join(
        f"<tr><td>{i}</td><td>{html.escape(r['platform'])}</td><td>{r['risk']}</td>"
        f"<td>{html.escape((r['text'] or '')[:50])}</td>"
        f"<td><a href='{_safe_href(r['url'])}' target=_blank rel=noopener>原帖</a> "
        f"<span class=muted>{html.escape(r['doc_id'])}</span></td></tr>"
        for i, r in enumerate(conn.execute(
            "SELECT c.platform,c.text,c.url,c.doc_id,f.risk FROM clean c JOIN features f USING(doc_id)"
            " WHERE f.polarity='neg' ORDER BY f.risk DESC LIMIT 20"), 1)
    ) or "<tr><td colspan=5 class=muted>暂无负面</td></tr>"

    body = (
        "<h1>舆情监控看板 <span class=muted>（数据只读）</span> "
        "<a href='/login' style='font-size:14px'>🔐 登录与采集</a> "
        "<a href='/watch' style='font-size:14px'>🎯 监控配置</a> "
        "<a href='/keywords' style='font-size:14px'>📖 关键词库</a> "
        "<a href='/exec' style='font-size:14px'>📊 高管概览</a> "
        "<a href='/dash' style='font-size:14px'>📈 战情室</a> "
        "<a href='/config' style='font-size:14px'>⚙️ 系统配置</a></h1>" + banner + review_line +
        "<h2>采集健康（各平台最近一次）</h2>"
        "<table><tr><th>平台</th><th>状态</th><th>条数</th><th>时间</th><th>备注</th></tr>"
        + health_rows + "</table>"
        "<h2>负面日趋势</h2><table><tr><th>日期</th><th>负面</th><th>总量</th><th></th></tr>"
        + trend_rows + "</table>"
        "<h2>报告历史</h2><table><tr><th>run_id</th><th>生成时间</th></tr>" + report_rows + "</table>"
        "<h2>负面 Top（按风险分）</h2>"
        "<table><tr><th>#</th><th>平台</th><th>风险</th><th>摘要</th><th>溯源</th></tr>"
        + neg_rows + "</table>"
    )
    return _page("舆情监控看板", body)


_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")


def _inline(s: str) -> str:
    """行内 markdown → HTML：先转义防 XSS，再还原 链接/粗体。href 仅允许 http(s)/相对路径。"""
    s = html.escape(s)
    def _lk(m):
        text, url = m.group(1), m.group(2)
        if url.startswith(("http://", "https://", "/")):   # 挡 javascript: 等，非法链接只留文字
            return f"<a href='{url}' target=_blank rel=noopener>{text}</a>"
        return text
    s = _LINK.sub(_lk, s)
    s = _BOLD.sub(r"<strong>\1</strong>", s)
    return s


def _md_table(rows: list) -> str:
    def cells(r): return [c.strip() for c in r.strip().strip("|").split("|")]
    def is_sep(r): return set(r.replace("|", "").strip()) <= set("-: ") and "-" in r
    header = cells(rows[0])
    th = "".join(f"<th>{_inline(c)}</th>" for c in header)
    body = "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in cells(r)) + "</tr>"
                   for r in rows[1:] if not is_sep(r))
    return f"<table><tr>{th}</tr>{body}</table>"


def md_to_html(md: str) -> str:
    """把本系统生成的报告 markdown 渲染成 HTML（针对固定子集：# ## > | --- ** []()）。"""
    lines, out, i = md.split("\n"), [], 0
    while i < len(lines):
        st = lines[i].strip()
        if not st:
            i += 1; continue
        if st.startswith("## "):
            out.append(f"<h2>{_inline(st[3:])}</h2>")
        elif st.startswith("# "):
            out.append(f"<h1>{_inline(st[2:])}</h1>")
        elif st.startswith(">"):
            out.append(f"<blockquote>{_inline(st.lstrip('> '))}</blockquote>")
        elif st.startswith("---"):
            out.append("<hr>")
        elif st.startswith("|"):
            block = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                block.append(lines[i].strip()); i += 1
            out.append(_md_table(block)); continue
        else:
            out.append(f"<p>{_inline(st)}</p>")
        i += 1
    return "\n".join(out)


def chart_data(store: Store, entity_id: str, watch: dict | None = None) -> dict:
    """图表页数据(纯JSON,供 Chart.js fetch)：情绪/声量趋势、方面口碑、话题、SOV、BHI趋势。"""
    from . import analytics
    from .report import aggregate, sov as sov_fn
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
    from . import load_watch
    if watch is None:
        try:
            watch = load_watch()
        except SystemExit:
            watch = {"platforms": [], "entities": []}
    selfs = _self_entities(watch)
    valid_ids = {eid for eid, _ in selfs} | {e["id"] for e in watch.get("entities", [])}
    if entity_id not in valid_ids:                # entity 来自 query param，只接受已知实体(防反射注入)
        entity_id = selfs[0][0] if selfs else ""
    tabs = " ".join(f"<a href='/dash?entity={html.escape(eid)}'>"
                    f"{'<b>' if eid == entity_id else ''}{html.escape(nm)}{'</b>' if eid == entity_id else ''}</a>"
                    for eid, nm in selfs)
    eid_js = json.dumps(entity_id).replace("</", "<\\/")   # 防 </script> 提前闭合(纵深防御)
    # KOL 榜 + 异常账号簇（服务端渲染表格，非图表）
    from . import analytics
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
    from . import load_watch, analytics
    from .report import sov as sov_fn, aggregate
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


def render_report(store: Store, run_id: str) -> str:
    row = store.conn.execute("SELECT markdown FROM reports WHERE run_id=?", (run_id,)).fetchone()
    if not row:
        return _page("未找到", "<p>未找到该报告。<a href='/'>返回</a></p>")
    return _page(run_id, f"<p><a href='/'>← 返回看板</a></p>{md_to_html(row['markdown'])}")


def render_keywords(store: Store, query_params: dict) -> str:
    """关键词库管理页面"""
    from .keywords import KeywordManager, TAGS

    km = KeywordManager(store)

    # 实体列表来自监控配置 watch.yaml（词库规划先于采集，故不从 clean 表推断）
    from . import load_watch
    try:
        entities = [(e["id"], (e.get("aliases") or [e["id"]])[0]) for e in load_watch().get("entities", [])]
    except SystemExit:                                           # 缺 PyYAML/配置时兜底
        entities = []
    if not entities:                                             # 配置读不到，退回已采集数据里的实体
        entities = [(r[0], r[0]) for r in store.conn.execute(
            'SELECT DISTINCT entity_id FROM clean WHERE entity_id IS NOT NULL').fetchall()]
    entity_ids = [eid for eid, _ in entities]
    current_entity = query_params.get('entity', [entity_ids[0] if entity_ids else None])[0]
    current_tag = query_params.get('tag', [''])[0]

    # 实体选择器
    entity_options = ''.join(
        f"<option value='{html.escape(eid)}' {'selected' if eid==current_entity else ''}>{html.escape(label)}</option>"
        for eid, label in entities)
    entity_select = f"<select id='entitySelect' onchange='location.href=\"/keywords?entity=\"+this.value'>{entity_options}</select>" if entities else "<span class=muted>无实体</span>"

    # 标签筛选（内置 8 类 + 该实体已用的自定义标签）
    custom_tags = [r[0] for r in store.conn.execute(
        'SELECT DISTINCT tag FROM keywords WHERE entity_id IS ? ORDER BY tag', (current_entity,)).fetchall()
        if r[0] not in TAGS]
    tag_filters = "<a href='/keywords?entity={}&tag='>全部</a>".format(current_entity or '')
    for tag_code, tag_name in list(TAGS.items()) + [(t, t) for t in custom_tags]:
        active = ' style="font-weight:bold"' if tag_code == current_tag else ''
        tag_filters += f" <a href='/keywords?entity={current_entity or ''}&tag={html.escape(tag_code)}'{active}>{html.escape(tag_name)}</a>"

    # 获取关键词列表
    keywords = km.list(tag=current_tag if current_tag else None, entity_id=current_entity)

    # 关键词表格
    kw_rows = ""
    for kw in keywords:
        tag_label = TAGS.get(kw['tag'], kw['tag'])
        source_label = '🤖AI' if kw['source'] == 'auto' else '👤人工'
        note_display = html.escape(kw['note'] or '')[:30] if kw['note'] else '-'
        kw_rows += f"""<tr>
            <td><input type='checkbox' class='rowchk' data-word="{html.escape(kw['word'])}" data-tag="{kw['tag']}"></td>
            <td>{html.escape(kw['word'])}</td>
            <td><span class='badge' style='background:#6e7781'>{tag_label}</span></td>
            <td>{kw['weight']:.2f}</td>
            <td>{source_label}</td>
            <td class=muted>{note_display}</td>
        </tr>"""

    if not kw_rows:
        kw_rows = "<tr><td colspan='6' class=muted>暂无关键词</td></tr>"

    # AI推荐列表
    suggestions = km.list_suggestions(status='pending', entity_id=current_entity)
    sug_rows = ""
    for sug in suggestions[:10]:
        tag_label = TAGS.get(sug['suggested_tag'], sug['suggested_tag'])
        reason = html.escape(sug['reason'] or '')[:50]
        sug_rows += f"""<tr>
            <td>{html.escape(sug['word'])}</td>
            <td><span class='badge' style='background:#0969da'>{tag_label}</span></td>
            <td>{sug['score']:.2f}</td>
            <td class=muted>{reason}</td>
            <td>
                <button onclick='approveSuggestion({sug["id"]})' style='background:#1a7f37;color:white;border:none;padding:4px 8px;border-radius:4px;cursor:pointer'>批准</button>
                <button onclick='rejectSuggestion({sug["id"]})' style='background:#cf222e;color:white;border:none;padding:4px 8px;border-radius:4px;cursor:pointer'>拒绝</button>
            </td>
        </tr>"""

    if not sug_rows:
        sug_rows = "<tr><td colspan='5' class=muted>暂无推荐</td></tr>"

    # 标签选项
    tag_options = ''.join(f"<option value='{code}'>{name}</option>" for code, name in TAGS.items())

    body = f"""
<h1>关键词库管理</h1>
<p><a href='/'>← 返回看板</a> ｜ <a href='/login'>登录与采集</a></p>

<div style='margin:20px 0;padding:15px;background:#f6f8fa;border-radius:6px'>
    <strong>选择实体：</strong> {entity_select}
    <button onclick='runAnalysis()' id='runBtn' style='margin-left:20px;background:#0969da;color:white;border:none;padding:6px 14px;border-radius:6px;cursor:pointer'>▶ 运行分析</button>
    <span id='runStatus' class=muted style='margin-left:10px'></span>
</div>

<div id='addForm' style='display:none;margin:20px 0;padding:15px;background:#fff;border:2px solid #0969da;border-radius:6px'>
    <h3>添加关键词</h3>
    <form onsubmit='return addKeyword(event)'>
        <table style='border:none'>
            <tr><td>词：</td><td><input type='text' id='word' required style='width:200px'></td></tr>
            <tr><td>标签：</td><td>
                <select id='tag' style='width:200px'>{tag_options}</select>
                <input type='text' id='customTag' maxlength='20' placeholder='或输入自定义标签' style='width:160px;margin-left:8px'>
            </td></tr>
            <tr><td>权重：</td><td><input type='number' id='weight' value='1.0' min='0' max='1' step='0.1' style='width:200px'></td></tr>
            <tr><td>备注：</td><td><input type='text' id='note' style='width:200px'></td></tr>
        </table>
        <button type='submit' style='background:#2da44e;color:white;border:none;padding:8px 16px;border-radius:6px;cursor:pointer'>添加</button>
        <button type='button' onclick='document.getElementById("addForm").style.display="none"' style='margin-left:10px;padding:8px 16px'>取消</button>
    </form>
</div>

<div style='margin:20px 0'>
    <strong>标签筛选：</strong> {tag_filters}
</div>

<div style='display:flex;justify-content:space-between;align-items:center;margin:20px 0 8px'>
    <h2 style='margin:0'>关键词列表 ({len(keywords)} 条)</h2>
    <div>
        <button onclick='document.getElementById("addForm").style.display="block"' style='background:#2da44e;color:white;border:none;padding:6px 14px;border-radius:6px;cursor:pointer'>+ 添加</button>
        <button onclick='deleteSelected()' style='margin-left:8px;background:#cf222e;color:white;border:none;padding:6px 14px;border-radius:6px;cursor:pointer'>🗑 删除选中</button>
    </div>
</div>
<table id='kwTable'>
    <thead><tr>
        <th><input type='checkbox' id='chkAll' onclick='toggleAll(this)'></th>
        <th>词</th>
        <th onclick='sortTable(2,"text")' style='cursor:pointer;user-select:none'>标签 ⇅</th>
        <th onclick='sortTable(3,"num")' style='cursor:pointer;user-select:none'>权重 ⇅</th>
        <th onclick='sortTable(4,"text")' style='cursor:pointer;user-select:none'>来源 ⇅</th>
        <th>备注</th>
    </tr></thead>
    <tbody>{kw_rows}</tbody>
</table>

<h2>AI推荐 ({len(suggestions)} 条待审核)</h2>
<table>
    <thead><tr><th>词</th><th>建议标签</th><th>分数</th><th>理由</th><th>操作</th></tr></thead>
    <tbody>{sug_rows}</tbody>
</table>

<script>
const currentEntity = '{current_entity or ''}';

function addKeyword(e) {{
    e.preventDefault();
    const data = {{
        action: 'add',
        word: document.getElementById('word').value,
        tag: document.getElementById('customTag').value.trim() || document.getElementById('tag').value,
        weight: document.getElementById('weight').value,
        note: document.getElementById('note').value,
        entity_id: currentEntity
    }};

    fetch('/api/keywords', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify(data)
    }})
    .then(r => r.json())
    .then(result => {{
        if (result.success) {{
            alert('添加成功');
            location.reload();
        }} else {{
            alert('添加失败: ' + result.message);
        }}
    }});
    return false;
}}

function toggleAll(cb) {{
    document.querySelectorAll('.rowchk').forEach(c => c.checked = cb.checked);
}}

function deleteSelected() {{
    const checks = [...document.querySelectorAll('.rowchk:checked')];
    if (!checks.length) {{ alert('请先勾选要删除的关键词'); return; }}
    if (!confirm('确认删除选中的 ' + checks.length + ' 个关键词?')) return;
    Promise.all(checks.map(c => {{
        const data = new URLSearchParams({{action: 'delete', word: c.dataset.word, tag: c.dataset.tag, entity_id: currentEntity}});
        return fetch('/api/keywords', {{method: 'POST', body: data}}).then(r => r.json());
    }})).then(results => {{
        const ok = results.filter(r => r.success).length;
        alert('已删除 ' + ok + '/' + results.length);
        location.reload();
    }});
}}

let sortDir = {{}};
function sortTable(col, type) {{
    const tbody = document.querySelector('#kwTable tbody');
    const rows = [...tbody.querySelectorAll('tr')].filter(r => r.querySelector('.rowchk'));
    if (!rows.length) return;
    const dir = sortDir[col] = -(sortDir[col] || 1);
    rows.sort((a, b) => {{
        let x = a.children[col].textContent.trim(), y = b.children[col].textContent.trim();
        if (type === 'num') return ((parseFloat(x) || 0) - (parseFloat(y) || 0)) * dir;
        return x.localeCompare(y, 'zh') * dir;
    }});
    rows.forEach(r => tbody.appendChild(r));
}}

function approveSuggestion(id) {{
    const data = new URLSearchParams({{action: 'approve', id: id}});
    fetch('/api/keywords', {{method: 'POST', body: data}})
    .then(r => r.json())
    .then(result => {{
        alert(result.message);
        if (result.success) location.reload();
    }});
}}

function rejectSuggestion(id) {{
    const data = new URLSearchParams({{action: 'reject', id: id}});
    fetch('/api/keywords', {{method: 'POST', body: data}})
    .then(r => r.json())
    .then(result => {{
        alert(result.message);
        if (result.success) location.reload();
    }});
}}

function setRunBtn(running) {{
    const btn = document.getElementById('runBtn');
    if (running) {{ btn.textContent = '⏸ 停止采集'; btn.style.background = '#cf222e'; btn.onclick = stopAnalysis; }}
    else {{ btn.textContent = '▶ 运行采集'; btn.style.background = '#0969da'; btn.onclick = runAnalysis; }}
}}

function runAnalysis() {{
    if (!confirm('确认运行一次网络内容分析？\\n流程：采集→分析→报告。需 opencli 已登录 + API key，可能耗时数分钟。')) return;
    fetch('/api/run', {{method: 'POST'}}).then(r => r.json()).then(() => pollRun());
}}

function stopAnalysis() {{
    if (!confirm('确认停止采集？将在完成当前平台后中止，已采数据保留。')) return;
    fetch('/api/run/stop', {{method: 'POST'}}).then(r => r.json()).then(d => {{
        document.getElementById('runStatus').innerHTML = '<span class="spin"></span>' + (d.message || '正在停止…');
        pollRun();
    }});
}}

function pollRun() {{
    fetch('/api/run/status').then(r => r.json()).then(d => {{
        const el = document.getElementById('runStatus');
        setRunBtn(d.running);
        if (d.running) {{
            el.innerHTML = '<span class="spin"></span>' + (d.current || '采集运行中…');
            setTimeout(pollRun, 1500);
        }} else if (d.last) {{
            el.textContent = (d.last.ok ? '✅ ' : '⚠️ ') + d.last.msg + ' (' + d.last.at + ')';
        }} else {{ el.textContent = ''; }}
    }});
}}
pollRun();   // 页面加载即同步一次状态（若正在跑则恢复轮询）
</script>
"""

    return _page("关键词库管理", body)


def render_login() -> str:
    """登录与采集页：桥状态 + 各平台登录态（JS 异步拉取）+ 一键开登录页 + 运行采集。"""
    # 纯静态外壳，行由 /api/login/status 异步填（heimao 走浏览器桥略慢，不阻塞页面）。
    # ponytail: runAnalysis/pollRun 与关键词页重复 ~15 行；两处调用不值得建共享 JS 资产管线。
    body = """
<h1>登录与采集</h1>
<p><a href='/'>← 返回看板</a> ｜ <a href='/watch'>监控配置</a> ｜ <a href='/keywords'>关键词库</a></p>
<p class=muted>采集复用你本机 Chrome 的登录会话。登录需人工扫码/短信（无法全自动），登录后点"运行采集"即可无人值守跑批。</p>

<div id='bridge' class=muted style='margin:12px 0'>检测浏览器桥…</div>

<h2>平台登录状态</h2>
<table>
    <thead><tr><th>平台</th><th>登录态</th><th>身份/备注</th><th>操作</th></tr></thead>
    <tbody id='loginRows'><tr><td colspan='4' class=muted>加载中…</td></tr></tbody>
</table>
<p><button onclick='refreshLogin()'>🔄 重新检测</button></p>

<div style='margin-top:24px;padding:15px;background:#f6f8fa;border-radius:6px'>
    <strong>登录好后：</strong>
    <button onclick='runAnalysis()' id='runBtn' style='margin-left:12px;background:#0969da;color:white;border:none;padding:6px 14px;border-radius:6px;cursor:pointer'>▶ 运行采集</button>
    <span id='runStatus' class=muted style='margin-left:10px'></span>
</div>

<script>
function badge(ok) {
    return ok ? '<span style="color:#1a7f37">✅ 已登录</span>' : '<span style="color:#8c8c8c">⬜ 未登录</span>';
}
function refreshLogin() {
    document.getElementById('bridge').textContent = '检测浏览器桥…';
    document.getElementById('loginRows').innerHTML = '<tr><td colspan="4" class=muted>检测中…（黑猫走浏览器桥略慢）</td></tr>';
    fetch('/api/login/status').then(r => r.json()).then(d => {
        document.getElementById('bridge').innerHTML = (d.bridge_ok ? '✅ ' : '❌ ') + d.bridge_msg;
        document.getElementById('loginRows').innerHTML = d.platforms.map(p => {
            const extra = p.identity || p.error || (p.method === 'browser' ? '浏览器探测' : '');
            return '<tr><td>' + p.platform + '</td><td>' + badge(p.logged_in) + '</td>'
                + '<td class=muted>' + extra + '</td>'
                + '<td><button onclick="openLogin(\\'' + p.platform + '\\')">打开登录页</button></td></tr>';
        }).join('') || '<tr><td colspan="4" class=muted>无需登录的平台</td></tr>';
    }).catch(() => { document.getElementById('bridge').textContent = '❌ 状态查询失败'; });
}
function openLogin(p) {
    fetch('/api/login/open', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({platform: p})})
    .then(r => r.json()).then(d => alert(d.message));
}
function setRunBtn(running) {
    const btn = document.getElementById('runBtn');
    if (running) { btn.textContent = '⏸ 停止采集'; btn.style.background = '#cf222e'; btn.onclick = stopAnalysis; }
    else { btn.textContent = '▶ 运行采集'; btn.style.background = '#0969da'; btn.onclick = runAnalysis; }
}
function runAnalysis() {
    if (!confirm('确认运行一次采集分析？\\n流程：采集→分析→报告，可能耗时数分钟。')) return;
    fetch('/api/run', {method: 'POST'}).then(r => r.json()).then(() => pollRun());
}
function stopAnalysis() {
    if (!confirm('确认停止采集？将在完成当前平台后中止，已采数据保留。')) return;
    fetch('/api/run/stop', {method: 'POST'}).then(r => r.json()).then(d => {
        document.getElementById('runStatus').innerHTML = '<span class="spin"></span>' + (d.message || '正在停止…');
        pollRun();
    });
}
function pollRun() {
    fetch('/api/run/status').then(r => r.json()).then(d => {
        const el = document.getElementById('runStatus');
        setRunBtn(d.running);
        if (d.running) {
            el.innerHTML = '<span class="spin"></span>' + (d.current || '采集运行中…');
            setTimeout(pollRun, 1500);
        } else if (d.last) {
            el.textContent = (d.last.ok ? '✅ ' : '⚠️ ') + d.last.msg + ' (' + d.last.at + ')';
        } else { el.textContent = ''; }
    });
}
refreshLogin();
pollRun();
</script>
"""
    return _page("登录与采集", body)


def _validate_watch(text: str) -> tuple[bool, str]:
    """校验 watch.yaml 文本：能解析 + 结构合法（platforms 列表 + entities 每个有 id）。返回 (ok, 说明)。"""
    import yaml
    try:
        d = yaml.safe_load(text)
    except yaml.YAMLError as e:
        return False, f"YAML 语法错误：{str(e)[:200]}"
    if not isinstance(d, dict):
        return False, "顶层必须是映射（含 platforms 和 entities）"
    if not isinstance(d.get("platforms"), list) or not d["platforms"]:
        return False, "缺少 platforms 列表（如 [weibo, zhihu, ...]）"
    ents = d.get("entities")
    if not isinstance(ents, list) or not ents:
        return False, "缺少 entities 列表（至少一个监控对象）"
    for i, e in enumerate(ents):
        if not isinstance(e, dict) or not e.get("id"):
            return False, f"第 {i+1} 个 entity 缺少 id"
        if e.get("aliases") is not None and not isinstance(e["aliases"], list):
            return False, f"entity {e.get('id')} 的 aliases 必须是列表"
    return True, f"✓ 合法：{len(d['platforms'])} 个平台 / {len(ents)} 个实体"


def render_watch() -> str:
    """监控配置编辑页：直接编辑 watch.yaml（唯一事实源）。保存前后端强校验，写前自动备份。"""
    from . import watch_path
    p = watch_path()
    try:
        with open(p, encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        content = f"# 读取失败：{e}"
    body = f"""
<h1>监控配置 <span class=muted>（watch.yaml，采集的搜索对象来源）</span></h1>
<p><a href='/'>← 返回看板</a> ｜ <a href='/login'>登录与采集</a> ｜ <a href='/keywords'>关键词库</a></p>
<p class=muted>生效文件：<code>{html.escape(p)}</code>；保存后下轮采集/刷新即生效，无需重启。写入前自动备份到 <code>watch.yaml.bak</code>。</p>

<details style='margin:8px 0'><summary class=muted style='cursor:pointer'>字段说明（点开）</summary>
<pre class=muted>platforms: [weibo, zhihu, xiaohongshu, douyin, bilibili, tieba, hupu, smzdm, weixin, heimao]
            # 只能填以上平台；决定采哪些站
entities:
  - id: youdoo            # 实体唯一标识（进 clean/报告/看板下拉）
    type: self            # self=报告主体+预警；competitor=只做SOV/情绪对比
    aliases: ["Youdoo Box","有度盒子"]   # aliases[0]=搜索词；全部别名用于相关性过滤（必须含其一）
    must_not: ["Doo Prime"]              # 命中即判非本品，硬排除同名歧义
    track_users: ["weibo:123"]           # 可选，定向抓这些账号
    crisis_boost: ["卡顿","死机","退货"]  # 该实体专属危机词，命中→风险×1.5</pre></details>

<textarea id='yaml' style='width:100%;height:420px;font:13px/1.5 Consolas,Menlo,monospace;padding:10px;border:1px solid #d0d7de;border-radius:6px'>{html.escape(content)}</textarea>
<p>
    <button onclick='saveWatch()' style='background:#2da44e;color:white;border:none;padding:8px 18px;border-radius:6px;cursor:pointer'>💾 保存</button>
    <button onclick='location.reload()' style='margin-left:8px;padding:8px 18px'>撤销更改</button>
    <span id='watchMsg' class=muted style='margin-left:12px'></span>
</p>

<script>
function saveWatch() {{
    const el = document.getElementById('watchMsg');
    el.textContent = '保存中…';
    fetch('/api/watch', {{method: 'POST', headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{content: document.getElementById('yaml').value}})}})
    .then(r => r.json()).then(d => {{
        el.innerHTML = (d.success ? '✅ ' : '⚠️ ') + d.message;
    }}).catch(e => {{ el.textContent = '⚠️ 请求失败'; }});
}}
</script>
"""
    return _page("监控配置", body)


def render_config(*, saved: bool = False, test_msg: str = "") -> str:
    from . import config
    rows = ""
    for k, label, secret, display, is_set in config.masked():
        if secret:
            ph = f"已设置 {html.escape(display)}，留空则不改" if is_set else "未设置"
            field = f"<input type=password name={k} placeholder='{ph}' autocomplete=off>"
        else:
            field = f"<input type=text name={k} value='{html.escape(display)}'>"
        rows += (f"<tr><td>{html.escape(label)}</td><td>{field}</td>"
                 f"<td class=muted>{'✓已配置' if is_set else '—'}</td></tr>")
    notice = "<p style='color:#1a7f37'>✓ 已保存</p>" if saved else ""
    if test_msg:
        notice += f"<p class=muted>{html.escape(test_msg)}</p>"
    tests = (" ｜ 测试连通："
             "<a href='/config/test?p=deepseek'>DeepSeek</a> "
             "<a href='/config/test?p=minimax'>MiniMax</a> "
             "<a href='/config/test?p=feishu'>飞书</a>")
    body = (
        "<h1>系统配置 <span class=muted>（本机写入，密钥仅存本地 yuqing_config.json）</span></h1>"
        f"<p><a href='/'>← 返回看板</a>{tests}</p>" + notice +
        "<form method=post action='/config'>"
        "<table><tr><th>配置项</th><th>值</th><th>状态</th></tr>" + rows + "</table>"
        "<p><button type=submit>保存</button> "
        "<span class=muted>密钥留空=保持原值；明文项留空=清除回退默认</span></p></form>"
        "<p class=muted>⚠️ 保存后需重跑批 <code>python -m yuqing.run</code> 生效。"
        "密钥以明文存本地文件（同 env 风险），已 gitignore 不进仓库。</p>")
    return _page("系统配置", body)


def _write_allowed(handler) -> bool:
    """写接口防护：仅本机 + 拒绝跨站（防 CSRF 篡改 base_url/webhook 窃取密钥）。

    - Host 须 localhost（挡 DNS rebinding）；
    - Sec-Fetch-Site 若存在须 same-origin/none（现代浏览器强制发送、JS 不可伪造）；
    - Origin 若存在须指向本机（老浏览器兜底）。
    """
    h = handler.headers
    host = (h.get("Host") or "").split(":")[0]
    if host not in ("127.0.0.1", "localhost"):
        return False
    sfs = h.get("Sec-Fetch-Site")
    if sfs and sfs not in ("same-origin", "none"):
        return False                              # 跨站请求，拒绝
    origin = h.get("Origin")
    if origin:
        oh = urlparse(origin).hostname
        if oh not in ("127.0.0.1", "localhost"):
            return False
    return True


def make_handler(db: str):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, body: str, code: int = 200):
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/config":
                self._send(render_config()); return
            if u.path == "/config/test":
                self._send(render_config(test_msg=_run_test(parse_qs(u.query).get("p", [""])[0]))); return
            if u.path == "/v2":
                # 新版前端（Ant Design + 完整架构）
                from pathlib import Path
                html_path = Path(__file__).parent / 'dashboard_v2.html'
                with open(html_path, 'r', encoding='utf-8') as f:
                    self._send(f.read())
                return
            if u.path == "/api/run/status":
                payload = json.dumps({"running": _run_state["running"], "last": _run_state["last"],
                                      "current": _run_state["current"]},
                                     ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload); return
            if u.path == "/api/login/status":
                from . import login, load_watch
                try:
                    platforms = load_watch().get("platforms", [])
                except SystemExit:
                    platforms = list(login.LOGIN_URLS)
                ok, msg = login.bridge_ok()
                payload = json.dumps({"bridge_ok": ok, "bridge_msg": msg,
                                      "platforms": login.status(platforms)},
                                     ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload); return
            if u.path == "/login":
                self._send(render_login()); return
            if u.path == "/watch":
                self._send(render_watch()); return
            store = Store(db)
            try:
                if u.path == "/":
                    body = render_index(store)
                elif u.path == "/exec":
                    body = render_exec(store)
                elif u.path == "/dash":
                    body = render_dash(store, parse_qs(u.query).get("entity", [""])[0])
                elif u.path == "/chart-data":
                    from . import load_watch
                    try:
                        w = load_watch()
                    except SystemExit:
                        w = None
                    payload = json.dumps(chart_data(store, parse_qs(u.query).get("entity", [""])[0], w),
                                         ensure_ascii=False).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload); return
                elif u.path == "/report":
                    body = render_report(store, parse_qs(u.query).get("run_id", [""])[0])
                elif u.path == "/keywords":
                    body = render_keywords(store, parse_qs(u.query))
                elif u.path == "/api/keywords":
                    # API: 返回JSON
                    from .keywords import KeywordManager
                    km = KeywordManager(store)
                    tag = parse_qs(u.query).get("tag", [None])[0]
                    entity_id = parse_qs(u.query).get("entity", [None])[0]
                    keywords = km.list(tag=tag, entity_id=entity_id)
                    suggestions = km.list_suggestions(status='pending', entity_id=entity_id)
                    payload = json.dumps({
                        'keywords': keywords,
                        'suggestions': suggestions
                    }, ensure_ascii=False, default=str).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload); return
                else:
                    self.send_error(404); return
            finally:
                store.close()
            self._send(body)

        def do_POST(self):
            u = urlparse(self.path)
            if u.path == "/config":
                if not _write_allowed(self):
                    self.send_error(403); return
                from . import config
                n = int(self.headers.get("Content-Length") or 0)
                form = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode("utf-8")).items()}
                config.save(form)
                self._send(render_config(saved=True))
            elif u.path == "/api/run":
                # 触发一次跑批（collect→analyze→report），后台线程执行，防并发
                if not _write_allowed(self):
                    self.send_error(403); return
                with _run_lock:
                    if _run_state["running"]:
                        result = {"running": True, "message": "已有分析在运行"}
                    else:
                        _run_state["running"] = True
                        _run_state["stop"] = False
                        _run_state["current"] = "正在启动…"
                        threading.Thread(target=_do_run, args=(db,), daemon=True).start()
                        result = {"running": True, "message": "已启动"}
                payload = json.dumps(result, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            elif u.path == "/api/run/stop":
                # 协作式停止：置标志，采集在下个平台边界中止（已采数据保留）
                if not _write_allowed(self):
                    self.send_error(403); return
                if _run_state["running"]:
                    _run_state["stop"] = True
                    _run_state["current"] = "正在停止…（完成当前平台后中止）"
                    result = {"success": True, "message": "已请求停止"}
                else:
                    result = {"success": False, "message": "当前无运行中的采集"}
                payload = json.dumps(result, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            elif u.path == "/api/login/open":
                # 在桥接 Chrome 打开某平台登录页（platform 白名单校验，防注入）
                if not _write_allowed(self):
                    self.send_error(403); return
                from . import login
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n).decode("utf-8")
                platform = (json.loads(raw).get("platform") if raw.startswith("{")
                            else parse_qs(raw).get("platform", [""])[0])
                try:
                    login.open_login(platform)
                    result = {"success": True, "message": f"已在浏览器打开 {platform} 登录页，请登录后点重新检测"}
                except Exception as e:
                    result = {"success": False, "message": str(e)[:200]}
                payload = json.dumps(result, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            elif u.path == "/api/watch":
                # 保存 watch.yaml：强校验 → 写前备份 .bak → 覆盖。校验不过绝不落盘（护单一事实源）
                if not _write_allowed(self):
                    self.send_error(403); return
                from . import watch_path
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n).decode("utf-8")
                try:
                    content = json.loads(raw).get("content", "") if raw.startswith("{") else ""
                except Exception:
                    content = ""
                ok, msg = _validate_watch(content)
                if ok:
                    try:
                        import shutil
                        p = watch_path()
                        try:
                            shutil.copyfile(p, p + ".bak")     # 覆盖前备份，防手滑丢配置
                        except FileNotFoundError:
                            pass
                        with open(p, "w", encoding="utf-8") as f:
                            f.write(content)
                        result = {"success": True, "message": msg + "，已保存（下轮采集/刷新生效）"}
                    except Exception as e:
                        result = {"success": False, "message": f"写入失败：{str(e)[:200]}"}
                else:
                    result = {"success": False, "message": msg + "（未保存）"}
                payload = json.dumps(result, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            elif u.path == "/api/keywords":
                # 关键词API：添加/删除/审核
                if not _write_allowed(self):
                    self.send_error(403); return
                store = Store(db)
                try:
                    from .keywords import KeywordManager
                    km = KeywordManager(store)
                    n = int(self.headers.get("Content-Length") or 0)
                    body_data = self.rfile.read(n).decode("utf-8")
                    data = json.loads(body_data) if body_data.startswith('{') else parse_qs(body_data)

                    # 处理表单格式
                    if isinstance(data, dict) and not isinstance(list(data.values())[0], list):
                        form = data
                    else:
                        form = {k: v[0] for k, v in data.items()}

                    action = form.get('action', '')
                    result = {'success': False, 'message': ''}

                    if action == 'add':
                        try:
                            km.add(
                                word=form['word'],
                                tag=form['tag'],
                                entity_id=form.get('entity_id') or None,
                                weight=float(form.get('weight', 1.0)),
                                note=form.get('note', '')
                            )
                            result = {'success': True, 'message': '添加成功'}
                        except Exception as e:
                            result = {'success': False, 'message': str(e)}

                    elif action == 'delete':
                        success = km.remove(
                            word=form['word'],
                            tag=form['tag'],
                            entity_id=form.get('entity_id') or None
                        )
                        result = {'success': success, 'message': '删除成功' if success else '未找到'}

                    elif action == 'approve':
                        success = km.approve_suggestion(int(form['id']))
                        result = {'success': success, 'message': '已批准' if success else '失败'}

                    elif action == 'reject':
                        success = km.reject_suggestion(int(form['id']))
                        result = {'success': success, 'message': '已拒绝' if success else '失败'}

                    payload = json.dumps(result, ensure_ascii=False).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                finally:
                    store.close()
            else:
                self.send_error(404)

        def log_message(self, *a):  # 静音默认日志
            pass
    return Handler


def _run_test(provider: str) -> str:
    """连通测试（供 /config/test）。"""
    if provider in ("deepseek", "minimax"):
        from . import llm
        ok, msg = llm.probe(provider)
        return f"{provider}：{msg}"
    if provider == "feishu":
        from .report import push_feishu
        try:
            ok = push_feishu("【测试】yuqing 系统配置连通测试", title="配置测试")
            return "飞书：已发送测试消息（去群里确认）" if ok else "飞书：未配置 Webhook"
        except Exception as e:
            return f"飞书：发送失败 {str(e)[:150]}"
    return "未知测试项"


def serve(db: str = "yuqing.db", host: str = "127.0.0.1", port: int = 8000) -> None:
    print(f"看板已启动（只读）：http://{host}:{port}  （Ctrl+C 停止）")
    # ThreadingHTTPServer: 每请求独立线程，慢接口（heimao 浏览器探测/跑批）不阻塞整站。
    # 每请求自建/关 SQLite 连接（见 do_GET），不跨线程共享，故线程安全。
    ThreadingHTTPServer((host, port), make_handler(db)).serve_forever()


if __name__ == "__main__":
    import sys
    serve(sys.argv[1] if len(sys.argv) > 1 else "yuqing.db")
