# -*- coding: utf-8 -*-
"""最小看板：报告历史 + 采集健康三态 + 负面 Top + 系统配置页。

ponytail: 数据视图只读 → stdlib http.server 直读 SQLite，零新依赖。/config 是唯一写入口
（表单存 yuqing_config.json），仅限本机（127.0.0.1 绑定 + Host 头校验）。
    python -m yuqing.dashboard yuqing.db      # 起服务，浏览器开 http://127.0.0.1:8000
"""

from __future__ import annotations

import html
import json
import mimetypes
import re
import threading
import io
import contextlib
import datetime as _dt
import secrets
import time
import urllib.request
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode, quote, unquote

from .store import Store

_WORKBENCH_DIR = Path(__file__).parent / "web" / "workbench"

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


def _start_background_run(db: str) -> dict:
    """Start at most one background run and return a version-neutral state shape."""
    with _run_lock:
        if _run_state["running"]:
            return {"running": True, "started": False, "message": "已有分析在运行"}
        _run_state["running"] = True
        _run_state["stop"] = False
        _run_state["current"] = "正在启动…"
        threading.Thread(target=_do_run, args=(db,), daemon=True).start()
        return {"running": True, "started": True, "message": "已启动"}


def _request_run_stop() -> dict:
    """Request cooperative stop at the next platform boundary."""
    with _run_lock:
        if not _run_state["running"]:
            return {"running": False, "stop_requested": False, "message": "当前无运行中的采集"}
        _run_state["stop"] = True
        _run_state["current"] = "正在停止…（完成当前平台后中止）"
        return {"running": True, "stop_requested": True, "message": "已请求停止"}

# ---- 飞书 OAuth 网页登录（stdlib 实现，零新依赖）----------------------------------
# 员工用企业飞书身份扫码/点击授权即可访问看板，替代 Nginx Basic Auth。
# session 持久化到 SQLite（与 yuqing.db 同库），Pod 重启后无需重新登录。
SESSION_COOKIE = "yuqing_sid"          # 会话 cookie 名
SESSION_TTL = 7 * 24 * 3600            # 会话有效期 7 天
_STATE_TTL = 600                       # OAuth state 有效期 10 分钟（防 CSRF + 记原始路径）
_FEISHU_BASE = "https://open.feishu.cn"
_oauth_states: dict[str, dict] = {}    # state -> {"next": 原始路径, "created_at": ts}

# ---- session SQLite 持久层（线程安全：每次调用独立连接，与业务 Store 相同模式）----
_SESSION_DB: str = ""   # 由 serve() 初始化为 db 路径


def _session_init(db_path: str) -> None:
    """建 sessions 表（首次启动时）。"""
    import sqlite3
    con = sqlite3.connect(db_path)
    try:
        con.execute("""
            CREATE TABLE IF NOT EXISTS yuqing_sessions (
                sid TEXT PRIMARY KEY,
                open_id TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                avatar_url TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL
            )
        """)
        con.commit()
    finally:
        con.close()


def _session_save(sid: str, user: dict) -> None:
    import sqlite3
    con = sqlite3.connect(_SESSION_DB)
    try:
        con.execute(
            "INSERT OR REPLACE INTO yuqing_sessions(sid,open_id,name,avatar_url,created_at)"
            " VALUES(?,?,?,?,?)",
            (sid, user.get("open_id", ""), user.get("name", ""),
             user.get("avatar_url", ""), time.time())
        )
        con.commit()
    finally:
        con.close()


def _session_load(sid: str) -> dict | None:
    import sqlite3
    con = sqlite3.connect(_SESSION_DB)
    try:
        row = con.execute(
            "SELECT open_id,name,avatar_url,created_at FROM yuqing_sessions WHERE sid=?", (sid,)
        ).fetchone()
    finally:
        con.close()
    if not row:
        return None
    open_id, name, avatar_url, created_at = row
    if time.time() - created_at > SESSION_TTL:
        _session_delete(sid)
        return None
    return {"open_id": open_id, "name": name, "avatar_url": avatar_url}


def _session_delete(sid: str) -> None:
    import sqlite3
    con = sqlite3.connect(_SESSION_DB)
    try:
        con.execute("DELETE FROM yuqing_sessions WHERE sid=?", (sid,))
        con.commit()
    finally:
        con.close()

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
    pending_incidents = len(store.list_incidents(status="pending_confirmation", limit=1000))
    incident_line = (f"<p style='color:#cf222e;font-weight:600'>🚨 待确认危机事件 <b>{pending_incidents}</b> 条 "
                     f"（<code>python -m yuqing.cli incidents pending_confirmation</code>）</p>"
                     if pending_incidents else "")
    annotated = store.annotated_count()
    annotate_line = (f"<p class=muted>📝 已标注 <b>{annotated}</b> 条 · "
                     f"<a href='/annotate'>去标注</a></p>")
    # 多维标签分布（Phase C：主体×立场，读 features.signals）
    from collections import Counter as _Ctr
    subj_c, stance_c = _Ctr(), _Ctr()
    for row in conn.execute("SELECT signals FROM features WHERE signals LIKE '%stance%'"):
        try:
            sg = json.loads(row["signals"])
            if sg.get("subject"):
                subj_c[sg["subject"]] += 1
            if sg.get("stance"):
                stance_c[sg["stance"]] += 1
        except Exception:
            pass
    if stance_c:
        dist = ("<p class=muted>🏷️ 主体：" + " · ".join(f"{k} {v}" for k, v in subj_c.most_common())
                + "　｜　立场：" + " · ".join(f"{k} {v}" for k, v in stance_c.most_common()) + "</p>")
    else:
        dist = "<p class=muted>🏷️ 多维标签：暂无（标注样本后重跑分析生效）</p>"
    dist_line = incident_line + annotate_line + dist
    from . import config as _cfg
    _m = _cfg.mode()
    _mode_label = "训练 training" if _m == "training" else "日常 daily"
    _mode_hint = "跑批不推飞书，适合调参/标注期" if _m == "training" else "跑批推报告到飞书"

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
        "<a href='/annotate' style='font-size:14px'>📝 标注</a> "
        "<a href='/watch' style='font-size:14px'>🎯 监控配置</a> "
        "<a href='/keywords' style='font-size:14px'>📖 关键词库</a> "
        "<a href='/exec' style='font-size:14px'>📊 高管概览</a> "
        "<a href='/dash' style='font-size:14px'>📈 战情室</a> "
        "<a href='/config' style='font-size:14px'>⚙️ 系统配置</a></h1>" + banner + review_line + dist_line +
        f"<p class=muted>运行模式：<b>{_mode_label}</b>（{_mode_hint}）</p>" +
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
    suggestions = km.list_suggestions(status='pending', entity_id=current_entity, exclude_tag='seed_alias')
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
<p><a href='/'>← 返回看板</a> ｜ <a href='/login'>登录与采集</a> ｜ <a href='/keywords'>关键词库</a> ｜ <a href='/accounts'>账号白名单</a></p>
<p class=muted>生效文件：<code>{html.escape(p)}</code>；保存后下轮采集/刷新即生效，无需重启。写入前自动备份到 <code>watch.yaml.bak</code>。</p>

<div style='margin:16px 0;padding:14px;background:#f6f8fa;border-radius:8px'>
  <b>🌱 种子词建议</b> <span class=muted>（系统挖的"产品词"，确认后写入 aliases 扩召回；评价词自动分流到 <a href='/keywords'>关键词库</a>）</span>
  <button onclick='mineSeeds()' style='margin-left:10px;padding:4px 12px;border-radius:6px;border:1px solid #d0d7de;cursor:pointer'>🔄 生成建议</button>
  <span id='mineMsg' class=muted style='margin-left:8px'></span>
  <table id='seedTbl' style='margin-top:8px;display:none'>
    <thead><tr><th>词</th><th>区分度</th><th>共现</th><th>来源</th><th>操作</th></tr></thead>
    <tbody id='seedRows'></tbody>
  </table>
</div>

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
function esc(s){{return (s||'').replace(/[&<>"]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c]));}}
function loadSeeds() {{
  fetch('/api/seed/list').then(r=>r.json()).then(d=>{{
    const rows=d.seeds||[]; const tb=document.getElementById('seedRows');
    document.getElementById('seedTbl').style.display = rows.length?'table':'none';
    tb.innerHTML = rows.map(s=>'<tr><td><b>'+esc(s.word)+'</b></td><td>×'+
      (s.reason||'').replace(/[^0-9.×]/g,'').slice(0,5)+'</td><td>'+esc(s.reason||'')+
      '</td><td class=muted>'+esc((s.source_docs||'').slice(0,30))+'</td><td>'+
      '<button onclick="seedAct('+s.id+',\\'approve\\')" style="background:#2da44e;color:#fff;border:none;padding:3px 10px;border-radius:5px;cursor:pointer">✓加入aliases</button> '+
      '<button onclick="seedAct('+s.id+',\\'reject\\')" style="padding:3px 8px">✗</button></td></tr>').join('');
    if(!rows.length) document.getElementById('mineMsg').textContent='暂无待确认种子词';
  }});
}}
function mineSeeds() {{
  document.getElementById('mineMsg').textContent='挖词中…（需 EMBED key + 已向量化数据）';
  fetch('/api/seed',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{action:'mine'}})}})
    .then(r=>r.json()).then(d=>{{ document.getElementById('mineMsg').textContent=d.message||''; loadSeeds(); }});
}}
function seedAct(id, action) {{
  fetch('/api/seed',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{action:action,id:id}})}})
    .then(r=>r.json()).then(d=>{{ document.getElementById('mineMsg').textContent=d.message||''; loadSeeds(); }});
}}
loadSeeds();
</script>
"""
    return _page("监控配置", body)


def render_accounts(store: Store) -> str:
    """官方账号白名单管理：登记官方/准官方/媒体账号，主体维确定性判定用。"""
    rows = ""
    for a in store.list_accounts():
        a = dict(a)
        rows += (f"<tr><td>{html.escape(a.get('platform') or '(全平台)')}</td>"
                 f"<td>{html.escape(a['author'])}</td><td>{html.escape(a['subject_type'])}</td>"
                 f"<td class=muted>{html.escape(a.get('entity_id') or '')}</td>"
                 f"<td><button onclick='delAcct({a['id']})'>✕</button></td></tr>")
    if not rows:
        rows = "<tr><td colspan=5 class=muted>暂无登记账号（未登记的一律判为 用户·KOL）</td></tr>"
    body = f"""
<h1>官方账号白名单 <span class=muted>（主体维确定性判定）</span></h1>
<p><a href='/'>← 返回看板</a> ｜ <a href='/watch'>监控配置</a> ｜ <a href='/annotate'>标注</a></p>
<p class=muted>登记官方/准官方/媒体账号，分析时命中即确定性判主体（盖过 LLM）；未登记默认"用户·KOL"。</p>
<div style='margin:14px 0;padding:12px;background:#f6f8fa;border-radius:8px'>
  <b>添加</b>：账号 <input id='au' placeholder='昵称，须与帖子作者一致' style='width:220px'>
  类型 <select id='st'><option>官方</option><option>准官方</option><option>媒体</option></select>
  平台 <input id='pf' placeholder='留空=全平台' style='width:90px'>
  <button onclick='addAcct()' style='background:#2da44e;color:#fff;border:none;padding:5px 14px;border-radius:6px;cursor:pointer'>+ 添加</button>
  <span id='acctMsg' class=muted style='margin-left:8px'></span>
</div>
<table><thead><tr><th>平台</th><th>账号</th><th>类型</th><th>实体</th><th></th></tr></thead>
<tbody>{rows}</tbody></table>
<script>
function addAcct() {{
  const au=document.getElementById('au').value.trim();
  if(!au){{document.getElementById('acctMsg').textContent='请填账号';return;}}
  fetch('/api/accounts',{{method:'POST',headers:{{'Content-Type':'application/json'}},
    body:JSON.stringify({{action:'add',author:au,subject_type:document.getElementById('st').value,platform:document.getElementById('pf').value.trim()}})}})
    .then(r=>r.json()).then(d=>{{ if(d.success) location.reload(); else document.getElementById('acctMsg').textContent=d.message||'失败'; }});
}}
function delAcct(id) {{
  if(!confirm('删除该账号登记？')) return;
  fetch('/api/accounts',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{action:'delete',id:id}})}})
    .then(r=>r.json()).then(d=>{{ if(d.success) location.reload(); }});
}}
</script>
"""
    return _page("账号白名单", body)


def render_annotate(store: Store, query_params: dict) -> str:
    """标注控制台（训练模式）：主动学习队列 + 多维标注 + 划选圈词。队列/写全走 fetch。"""
    from . import load_watch
    from .keywords import SUBJECTS, STANCES, IMPORTANCE, TAGS
    try:
        entities = [(e["id"], (e.get("aliases") or [e["id"]])[0]) for e in load_watch().get("entities", [])]
    except SystemExit:
        entities = [(r[0], r[0]) for r in store.conn.execute(
            "SELECT DISTINCT entity_id FROM clean WHERE entity_id IS NOT NULL").fetchall()]
    cur = query_params.get("entity", [entities[0][0] if entities else ""])[0]
    ent_opts = "".join(f"<option value='{html.escape(e)}' {'selected' if e==cur else ''}>{html.escape(nm)}</option>"
                       for e, nm in entities)
    subj_opts = "".join(f"<label><input type=radio name=subject value='{s}'>{s}</label> " for s in SUBJECTS)
    stance_opts = "".join(f"<label><input type=radio name=stance value='{s}'>{s}</label> " for s in STANCES)
    imp_opts = "".join(f"<label><input type=radio name=importance value='{s}'>{s}</label> " for s in IMPORTANCE)
    role_opts = "".join(f"<option value='{c}'>{n}</option>" for c, n in TAGS.items())
    done = store.annotated_count()
    body = f"""
<h1>标注控制台 <span class=muted>（训练模式）</span></h1>
<p><a href='/'>← 返回看板</a> ｜ <a href='/keywords'>关键词库</a> ｜ <a href='/watch'>监控配置</a>
   ｜ 累计已标注 <b>{done}</b> 条</p>
<p class=muted>系统按"最没把握+高影响+多样"挑代表样本给你标；圈选的词会进关键词库待审，产品名会进种子建议。</p>

<div style='margin:12px 0'>实体：
  <select id='entSel' onchange='location.href="/annotate?entity="+this.value'>{ent_opts}</select>
  <span id='queueInfo' class=muted style='margin-left:12px'>加载队列…</span>
</div>

<div id='card' class='card' style='border:1px solid #d0d7de;border-radius:8px;padding:16px;display:none'>
  <div id='meta' class=muted></div>
  <div style='margin:6px 0'><b>采样原因：</b><span id='reason'></span></div>
  <div id='body' style='background:#f6f8fa;padding:12px;border-radius:6px;line-height:1.9;user-select:text;cursor:text'></div>
  <div class=muted style='margin:6px 0'>机器判定(对照)：<span id='mach'></span></div>
  <hr>
  <div style='margin:8px 0'><b>主体：</b>{subj_opts}</div>
  <div style='margin:8px 0'><b>立场：</b>{stance_opts}</div>
  <div style='margin:8px 0'><b>重要性：</b>{imp_opts} <span class=muted>（预填，可改）</span></div>
  <div style='margin:8px 0'><b>圈选的词</b> <span class=muted>（在正文里鼠标划选自动加行）</span>：
    <div id='words'></div>
    <template id='wordRow'>
      <div style='margin:4px 0'>「<span class=w></span>」→
        <select class=role>{role_opts}</select>
        <button type=button class=del>✕</button></div>
    </template>
  </div>
  <div style='margin:8px 0'>备注：<input id='note' style='width:60%'></div>
  <div style='margin-top:12px'>
    <button onclick='save()' style='background:#2da44e;color:#fff;border:none;padding:8px 18px;border-radius:6px;cursor:pointer'>保存并下一条 ▶</button>
    <button onclick='nextCard()' style='margin-left:8px;padding:8px 18px'>跳过</button>
    <span id='msg' class=muted style='margin-left:12px'></span>
  </div>
</div>
<div id='empty' class=muted style='display:none;padding:40px;text-align:center'>🎉 该实体暂无待标样本</div>

<script>
const ENTITY='{cur}';
let queue=[], cur_=null;
function esc(s){{return (s||'').replace(/[&<>]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;'}}[c]));}}
function loadQueue(){{
  fetch('/api/annotate/queue?entity='+encodeURIComponent(ENTITY)).then(r=>r.json()).then(d=>{{
    queue=d.queue||[]; document.getElementById('queueInfo').textContent='待标 '+queue.length+' 条';
    nextCard();
  }});
}}
function nextCard(){{
  document.querySelectorAll('input[type=radio]').forEach(x=>x.checked=false);
  document.getElementById('words').innerHTML=''; document.getElementById('note').value='';
  document.getElementById('msg').textContent='';
  if(!queue.length){{document.getElementById('card').style.display='none';document.getElementById('empty').style.display='block';return;}}
  cur_=queue.shift();
  document.getElementById('card').style.display='block'; document.getElementById('empty').style.display='none';
  document.getElementById('meta').innerHTML='['+esc(cur_.platform)+'] @'+esc(cur_.author||'?')+' 粉'+(cur_.author_followers||0)+' · '+esc(cur_.publish_ts||'')+' · <a href="'+esc(cur_.url||'#')+'" target=_blank>原文</a>';
  document.getElementById('reason').textContent=cur_.reason||'';
  document.getElementById('body').textContent=cur_.text||'';
  document.getElementById('mach').textContent=(cur_.polarity||'?')+' / 置信'+(cur_.confidence??'?')+' / risk'+(cur_.risk??'?');
  // 重要性预填
  const imp = (cur_.risk>=40)?'高':(cur_.risk>=10)?'中':'低';
  const el=[...document.querySelectorAll('input[name=importance]')].find(x=>x.value===imp); if(el)el.checked=true;
  document.getElementById('queueInfo').textContent='待标 '+queue.length+' 条';
}}
// 划选圈词
document.getElementById('body').addEventListener('mouseup',()=>{{
  const w=(window.getSelection().toString()||'').trim();
  if(!w||w.length>20)return;
  const t=document.getElementById('wordRow').content.cloneNode(true);
  t.querySelector('.w').textContent=w;
  t.querySelector('.del').onclick=e=>e.target.closest('div').remove();
  document.getElementById('words').appendChild(t);
  window.getSelection().removeAllRanges();
}});
function radioVal(n){{const e=document.querySelector('input[name='+n+']:checked');return e?e.value:null;}}
function save(){{
  const words=[...document.querySelectorAll('#words > div')].map(d=>({{word:d.querySelector('.w').textContent, role:d.querySelector('.role').value}}));
  const payload={{doc_id:cur_.doc_id, entity_id:cur_.entity_id||ENTITY, sample_source:'active',
    subject:radioVal('subject'), stance:radioVal('stance'), importance:radioVal('importance'),
    picked_words:words, note:document.getElementById('note').value}};
  if(!payload.subject||!payload.stance){{document.getElementById('msg').textContent='请至少选主体和立场';return;}}
  fetch('/api/annotate',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(payload)}})
    .then(r=>r.json()).then(d=>{{ if(d.success) nextCard(); else document.getElementById('msg').textContent='保存失败: '+(d.message||''); }});
}}
loadQueue();
</script>
"""
    return _page("标注控制台", body)

def render_config(*, saved: bool = False, test_msg: str = "") -> str:
    from . import config
    rows = ""
    for k, label, secret, display, is_set in config.masked():
        if k == "YUQING_MODE":
            cur = display or "daily"
            opts = "".join(f"<option value='{m}' {'selected' if m==cur else ''}>{m}</option>"
                           for m in ("daily", "training"))
            field = f"<select name={k}>{opts}</select>"
        elif secret:
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


# ---- 飞书 OAuth：会话管理 + 飞书 API 调用（全部走 urllib，无第三方依赖）------------

def _new_session(user: dict) -> str:
    """建会话：随机 sid（secrets.token_urlsafe(32)），持久化到 SQLite。返回 sid。"""
    sid = secrets.token_urlsafe(32)
    _session_save(sid, user)
    return sid


def _get_session(sid: str) -> dict | None:
    """按 sid 取有效会话的 user_info；超 7 天则清理并返回 None（TTL 检查）。"""
    return _session_load(sid)


def _sid_from_cookie(handler) -> str:
    """从请求 Cookie 头解析 yuqing_sid（缺失/畸形返回空串）。"""
    raw = handler.headers.get("Cookie")
    if not raw:
        return ""
    jar = SimpleCookie()
    try:
        jar.load(raw)
    except Exception:
        return ""
    m = jar.get(SESSION_COOKIE)
    return m.value if m else ""


def _require_auth(handler) -> dict | None:
    """从 Cookie 读 sid，验证 session 有效。返回 user_info dict（含 name/open_id）或 None（未登录）。"""
    sid = _sid_from_cookie(handler)
    return _get_session(sid) if sid else None


def _cookie_header(sid: str, *, clear: bool = False) -> str:
    """构造 Set-Cookie：HttpOnly + SameSite=Lax + Path=/。clear=True 时立即过期以登出。"""
    parts = [f"{SESSION_COOKIE}={'' if clear else sid}", "Path=/", "HttpOnly", "SameSite=Lax"]
    try:
        from . import config
        if config.resolve("FEISHU_REDIRECT_URI").lower().startswith("https://"):
            parts.append("Secure")
    except Exception:
        pass
    parts.append("Max-Age=0" if clear else f"Max-Age={SESSION_TTL}")
    return "; ".join(parts)


def _safe_next(nxt: str) -> str:
    """防开放重定向：next 仅接受本站绝对路径（/ 开头且非 //），否则回首页。"""
    return nxt if (nxt.startswith("/") and not nxt.startswith("//")) else "/"


def _new_state(next_path: str) -> str:
    """生成一次性 OAuth state（防 CSRF）并记住登录后要跳回的原始路径；顺带清理过期 state。"""
    now = time.time()
    for k in [k for k, v in _oauth_states.items() if now - v["created_at"] > _STATE_TTL]:
        _oauth_states.pop(k, None)
    state = secrets.token_urlsafe(16)
    _oauth_states[state] = {"next": _safe_next(next_path), "created_at": now}
    return state


def _feishu_app_access_token() -> str:
    """取 app_access_token（自建应用），用于给 code 换 user_token 时的 Bearer 鉴权。

    飞书 API：POST /open-apis/auth/v3/app_access_token/internal  body: {app_id, app_secret}
    """
    from . import config
    payload = {"app_id": config.resolve("FEISHU_APP_ID"),
               "app_secret": config.resolve("FEISHU_APP_SECRET")}
    req = urllib.request.Request(
        f"{_FEISHU_BASE}/open-apis/auth/v3/app_access_token/internal",
        data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        r = json.loads(resp.read().decode("utf-8"))
    if r.get("code") != 0:
        raise RuntimeError(f"app_access_token 失败：{r.get('msg') or r}")
    return r["app_access_token"]


def _feishu_user_access_token(code: str) -> str:
    """用授权码换 user_access_token。

    飞书 API：POST /open-apis/authen/v1/oidc/access_token
      Header: Authorization: Bearer <app_access_token>
      body:   {grant_type: "authorization_code", code}
    """
    app_token = _feishu_app_access_token()
    req = urllib.request.Request(
        f"{_FEISHU_BASE}/open-apis/authen/v1/oidc/access_token",
        data=json.dumps({"grant_type": "authorization_code", "code": code}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {app_token}"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        r = json.loads(resp.read().decode("utf-8"))
    if r.get("code") != 0:
        raise RuntimeError(f"换取 user_access_token 失败：{r.get('msg') or r}")
    return r["data"]["access_token"]


def _feishu_user_info(user_access_token: str) -> dict:
    """用 user_access_token 取登录用户信息，精简为 {open_id, name, avatar_url}。

    飞书 API：GET /open-apis/authen/v1/user_info  Header: Authorization: Bearer <user_access_token>
    """
    req = urllib.request.Request(
        f"{_FEISHU_BASE}/open-apis/authen/v1/user_info",
        headers={"Authorization": f"Bearer {user_access_token}"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        r = json.loads(resp.read().decode("utf-8"))
    if r.get("code") != 0:
        raise RuntimeError(f"获取用户信息失败：{r.get('msg') or r}")
    d = r.get("data") or {}
    return {"open_id": d.get("open_id", ""), "name": d.get("name", ""),
            "avatar_url": d.get("avatar_url", "")}


def _feishu_authorize_url(app_id: str, redirect_uri: str, state: str) -> str:
    """拼飞书授权页 URL（用户在此扫码/点击授权）。

    飞书 API：GET /open-apis/authen/v1/authorize?app_id&redirect_uri&scope&state
    """
    q = urlencode({"app_id": app_id, "redirect_uri": redirect_uri,
                   "scope": "contact:user.base:readonly", "state": state})
    return f"{_FEISHU_BASE}/open-apis/authen/v1/authorize?{q}"


def render_auth_hint() -> str:
    """飞书应用未配置时的友好提示页（DoD#3：不 500）。"""
    body = ("<h1>飞书登录未配置</h1>"
            "<p>本看板通过飞书企业应用登录。管理员尚未配置飞书应用凭据，暂时无法登录。</p>"
            "<p>请在<strong>本机</strong>打开 <a href='/config'>⚙️ 系统配置</a>，填写 "
            "<b>飞书应用 App ID</b> / <b>App Secret</b> / <b>回调地址</b> 后重试。</p>"
            "<p class=muted>对应配置项：FEISHU_APP_ID · FEISHU_APP_SECRET · FEISHU_REDIRECT_URI</p>")
    return _page("飞书登录未配置", body)


def render_auth_error(msg: str) -> str:
    """飞书登录流程出错时的友好提示页（不 500）。"""
    body = (f"<h1>登录失败</h1><p>{html.escape(msg)}</p>"
            "<p><a href='/auth/login'>← 重新登录</a></p>")
    return _page("登录失败", body)


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


def _normalized_origin(value: str, *, allow_path: bool = False) -> tuple[str, str, int] | None:
    """把 http(s) URL 规范为 (scheme, host, port)，非法值返回 None。"""
    try:
        p = urlparse((value or "").strip())
        if p.scheme.lower() not in ("http", "https") or not p.hostname:
            return None
        if p.username is not None or p.password is not None or p.params or p.query or p.fragment:
            return None
        if not allow_path and p.path not in ("", "/"):
            return None
        scheme = p.scheme.lower()
        port = p.port or (443 if scheme == "https" else 80)
    except ValueError:
        return None
    return scheme, p.hostname.rstrip(".").lower(), port


def _forwarded_origin(handler, request_origin: tuple[str, str, int]) -> tuple[str, str, int] | None:
    """读取代理提供的公开 origin；没有完整信息时返回 None。

    X-Forwarded-* 可能是逗号分隔的代理链，第一个值是客户端最初访问的公开端点。
    这些值只作为代理一致性校验；配置了 OAuth 回调时，最终信任锚仍是该回调地址。
    """
    h = handler.headers
    host = (h.get("X-Forwarded-Host") or "").split(",", 1)[0].strip()
    if not host:
        return None
    proto = (h.get("X-Forwarded-Proto") or "").split(",", 1)[0].strip().lower()
    if not proto:
        proto = request_origin[0]
    port = (h.get("X-Forwarded-Port") or "").split(",", 1)[0].strip()
    if port and ":" not in host:
        host = f"{host}:{port}"
    return _normalized_origin(f"{proto}://{host}")


def _configured_oauth_origin() -> tuple[str, str, int] | None:
    """OAuth 回调地址是远程站点公开 origin 的受信配置来源。"""
    try:
        from . import config
        return _normalized_origin(config.resolve("FEISHU_REDIRECT_URI"), allow_path=True)
    except Exception:
        return None


def _mutation_allowed(handler) -> bool:
    """业务写接口：本机沿用旧保护；远程必须 OAuth 登录且公开 origin 同源。"""
    if _write_allowed(handler):
        return True
    if _require_auth(handler) is None:
        return False
    h = handler.headers
    sfs = (h.get("Sec-Fetch-Site") or "").lower()
    if sfs and sfs not in ("same-origin", "none"):
        return False

    # 浏览器同源 POST/fetch 会带 Origin。远程写操作不接受缺失/畸形 Origin，
    # 避免仅凭可伪造的 Host 或缺失 Sec-Fetch-Site 就绕过 CSRF 校验。
    origin = _normalized_origin(h.get("Origin") or "")
    if origin is None:
        return False

    # TLS 常在 Ingress/ELB 终止，后端 Host 可能已被改成 service:port；不能把它
    # 与浏览器看到的公开 Origin 直接比较。优先以 OAuth 回调地址作为可信公开
    # origin，并在代理明确提供 X-Forwarded-Host 时校验二者一致。
    configured = _configured_oauth_origin()
    forwarded = _forwarded_origin(handler, origin)
    has_forwarded_host = bool(h.get("X-Forwarded-Host"))
    if configured is not None:
        if origin != configured or (has_forwarded_host and forwarded != configured):
            return False
        return True

    # 无回调配置时保留可测试/直连部署能力：有代理头则与公开代理端点比；否则
    # 用 Origin 的 scheme 加原始 Host 严格比较 scheme/host/port。
    if forwarded is not None:
        return origin == forwarded
    host = (h.get("Host") or "").strip()
    return origin == _normalized_origin(f"{origin[0]}://{host}")


def make_handler(db: str):
    class Handler(BaseHTTPRequestHandler):
        def end_headers(self):
            """Keep legacy JSON APIs available while advertising the versioned successor."""
            path = urlparse(getattr(self, "path", "")).path
            if path.startswith("/api/") and not path.startswith("/api/v1/"):
                self.send_header("Deprecation", "true")
                self.send_header("Link", '</api/v1>; rel="successor-version"')
            super().end_headers()

        def _send(self, body: str, code: int = 200):
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_bytes(self, data: bytes, content_type: str, code: int = 200):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def _send_workbench_asset(self, asset_name: str) -> None:
            """Serve one packaged asset without allowing path traversal."""
            decoded = unquote(asset_name)
            relative = Path(decoded)
            if (not decoded or "\x00" in decoded or relative.is_absolute()
                    or decoded.startswith(("/", "\\"))):
                self.send_error(404)
                return
            root = _WORKBENCH_DIR.resolve()
            candidate = (root / relative).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                self.send_error(404)
                return
            if not candidate.is_file():
                self.send_error(404)
                return
            content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
            if content_type.startswith("text/") or content_type in ("application/javascript", "application/json"):
                content_type += "; charset=utf-8"
            self._send_bytes(candidate.read_bytes(), content_type)

        def _send_json(self, payload: dict, code: int = 200):
            data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def _send_api_error(self, code: str, message: str, status: int):
            from .api.responses import error_payload
            self._send_json(error_payload(code, message), status)

        def _api_principal(self) -> dict | None:
            """Return one reusable identity shape for local and OAuth API reads."""
            if _write_allowed(self):
                return {"open_id": "local", "name": "本机用户", "auth_type": "local"}
            user = _require_auth(self)
            return ({**user, "auth_type": "oauth"} if user else None)

        def _api_mutation_allowed(self) -> bool:
            """Expose the existing session/origin/forwarded-host checks to v1 routes."""
            return _mutation_allowed(self)

        def _handle_api_v1_get(self, u) -> None:
            from .api.overview import RANGES, build_overview, configured_entities, resolve_entity
            from .api.responses import APIError, enum_value, query_value, success_payload

            if u.path == "/api/v1/readiness":
                try:
                    from . import load_watch
                    from . import collector_client
                    watch = load_watch()
                    store = Store(db)
                    try:
                        data, quality, notes = build_overview(store, watch, range_name="7d")
                        schema_version = store.schema_version()
                    finally:
                        store.close()
                    if schema_version != 2 or not (_WORKBENCH_DIR / "index.html").is_file():
                        raise RuntimeError("delivery baseline unavailable")
                    collector = (
                        collector_client.selfcheck(timeout=5)
                        if collector_client.enabled() else None
                    )
                except Exception:
                    self._send_api_error("NOT_READY", "服务尚未就绪", 503)
                    return
                readiness = {"ready": True, "schema_version": schema_version}
                if collector is not None:
                    readiness["collector"] = collector
                self._send_json(success_payload(
                    readiness,
                    entity_id=data["entity"]["id"], data_quality=quality,
                    quality_notes=notes,
                ))
                return
            if self._api_principal() is None:
                self._send_api_error("UNAUTHORIZED", "请先登录", 401)
                return
            incident_match = re.fullmatch(r"/api/v1/incidents/([0-9A-Za-z_-]+)", u.path)
            report_match = re.fullmatch(r"/api/v1/reports/([^/]+)", u.path)
            document_match = re.fullmatch(r"/api/v1/docs/([0-9a-f]{6,16})", u.path)
            if u.path not in {
                "/api/v1/overview", "/api/v1/collection/status",
                "/api/v1/collection/login-status", "/api/v1/analysis", "/api/v1/incidents",
                "/api/v1/backlog", "/api/v1/backlog.csv", "/api/v1/reviews", "/api/v1/reports",
                "/api/v1/context", "/api/v1/watch", "/api/v1/keywords", "/api/v1/seeds",
            } and incident_match is None and report_match is None and document_match is None:
                self._send_api_error("NOT_FOUND", "接口不存在", 404)
                return

            query = parse_qs(u.query, keep_blank_values=True)
            try:
                requested_entity = query_value(query, "entity_id")
                from . import load_watch
                watch = load_watch()
                if u.path == "/api/v1/context":
                    resolved_id, entity_name = resolve_entity(watch, requested_entity)
                    data = {
                        "entity": {"id": resolved_id, "name": entity_name},
                        "entities": configured_entities(watch),
                        "ranges": [
                            {"value": "7d", "label": "近 7 天"},
                            {"value": "30d", "label": "近 30 天"},
                            {"value": "90d", "label": "近 90 天"},
                        ],
                        "user": self._api_principal(),
                    }
                    quality, quality_notes = "ok", []
                elif u.path in {"/api/v1/watch", "/api/v1/keywords", "/api/v1/seeds"}:
                    from .api.watch import build_keywords, build_seeds, build_watch_config
                    if u.path == "/api/v1/watch":
                        data = build_watch_config(watch, entity_id=requested_entity)
                    else:
                        store = Store(db)
                        try:
                            data = (
                                build_keywords(store, watch, entity_id=requested_entity)
                                if u.path == "/api/v1/keywords"
                                else build_seeds(store, watch, entity_id=requested_entity)
                            )
                        finally:
                            store.close()
                    quality, quality_notes = "ok", []
                elif u.path == "/api/v1/reviews":
                    from .api.reviews import CONFIDENCE_BUCKETS, REVIEW_STATUSES, build_reviews
                    status = enum_value(query, "status", REVIEW_STATUSES, default="pending")
                    confidence = enum_value(
                        query, "confidence", CONFIDENCE_BUCKETS, default="all",
                    )
                    platform = query_value(query, "platform")
                    limit = query_value(query, "limit")
                    cursor = query_value(query, "cursor")
                    store = Store(db)
                    try:
                        data, quality, quality_notes = build_reviews(
                            store, watch, entity_id=requested_entity, status=status,
                            platform=platform, confidence=confidence, limit=limit, cursor=cursor,
                        )
                    finally:
                        store.close()
                elif u.path in {"/api/v1/overview", "/api/v1/analysis"}:
                    range_name = enum_value(query, "range", RANGES, default="7d")
                    store = Store(db)
                    try:
                        if u.path == "/api/v1/overview":
                            data, quality, quality_notes = build_overview(
                                store, watch, entity_id=requested_entity, range_name=range_name,
                            )
                        else:
                            from .api.analysis import build_analysis
                            data, quality, quality_notes = build_analysis(
                                store, watch, entity_id=requested_entity, range_name=range_name,
                            )
                    finally:
                        store.close()
                elif u.path == "/api/v1/collection/status":
                    from . import login
                    from .api.collection import build_collection_status
                    include_login = enum_value(query, "include_login", ("0", "1"), default="1") == "1"

                    def _login_provider(platforms):
                        return login.bridge_ok(), login.status(platforms)

                    store = Store(db)
                    try:
                        data, quality, quality_notes = build_collection_status(
                            store, watch, dict(_run_state), entity_id=requested_entity,
                            login_provider=_login_provider if include_login else None,
                        )
                    finally:
                        store.close()
                elif u.path == "/api/v1/collection/login-status":
                    from . import login
                    from .api.collection import execution_environment
                    resolved_id, entity_name = resolve_entity(watch, requested_entity)
                    platforms = [str(item) for item in (watch.get("platforms") or [])]
                    bridge_ok, bridge_message = login.bridge_ok()
                    data = {
                        "entity": {"id": resolved_id, "name": entity_name},
                        "execution": execution_environment(),
                        "bridge": {"ok": bridge_ok, "message": bridge_message},
                        "platforms": login.status(platforms),
                    }
                    quality = "ok" if bridge_ok else "degraded"
                    quality_notes = [] if bridge_ok else [bridge_message]
                elif u.path in {"/api/v1/backlog", "/api/v1/backlog.csv"}:
                    from .api.backlog import backlog_csv as build_backlog_csv, build_backlog
                    range_name = enum_value(query, "range", RANGES, default="30d")
                    if u.path == "/api/v1/backlog.csv":
                        store = Store(db)
                        try:
                            csv_text, csv_entity_id = build_backlog_csv(
                                store, watch, entity_id=requested_entity, range_name=range_name,
                            )
                        finally:
                            store.close()
                        data = {"_csv": csv_text, "entity": {"id": csv_entity_id}}
                        quality, quality_notes = "ok", []
                    else:
                        store = Store(db)
                        try:
                            data, quality, quality_notes = build_backlog(
                                store, watch, entity_id=requested_entity, range_name=range_name,
                            )
                        finally:
                            store.close()
                elif u.path == "/api/v1/reports" or report_match is not None or document_match is not None:
                    from .api.reports import (
                        build_report_detail, build_report_list, build_source_document,
                    )
                    store = Store(db)
                    try:
                        if report_match is not None:
                            data, quality, quality_notes = build_report_detail(
                                store, watch, unquote(report_match.group(1)),
                                entity_id=requested_entity,
                            )
                        elif document_match is not None:
                            data, quality, quality_notes = build_source_document(
                                store, watch, document_match.group(1),
                                entity_id=requested_entity,
                            )
                        else:
                            data, quality, quality_notes = build_report_list(
                                store, watch, entity_id=requested_entity,
                            )
                    finally:
                        store.close()
                else:
                    from .api.incidents import build_incident_detail, build_incident_list
                    store = Store(db)
                    try:
                        if incident_match is not None:
                            data, quality, quality_notes = build_incident_detail(
                                store, watch, incident_match.group(1),
                            )
                        else:
                            status = query_value(query, "status")
                            data, quality, quality_notes = build_incident_list(
                                store, watch, entity_id=requested_entity, status=status,
                            )
                    finally:
                        store.close()
            except APIError as exc:
                self._send_api_error(exc.code, exc.message, exc.status)
                return
            except (SystemExit, OSError, ValueError, TypeError):
                self._send_api_error("CONFIG_ERROR", "监控配置无法读取", 503)
                return
            except Exception:
                self._send_api_error("INTERNAL_ERROR", "服务暂时不可用", 500)
                return

            if u.path == "/api/v1/backlog.csv":
                csv_data = data["_csv"].encode("utf-8-sig")
                self.send_response(200)
                self.send_header("Content-Type", "text/csv; charset=utf-8")
                self.send_header("Content-Disposition", 'attachment; filename="yuqing-backlog.csv"')
                self.send_header("Content-Length", str(len(csv_data)))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(csv_data)
                return
            self._send_json(success_payload(
                data,
                entity_id=data["entity"]["id"],
                data_quality=quality,
                quality_notes=quality_notes,
            ))

        def _handle_api_v1_post(self, u) -> None:
            from .api.collection import execution_environment
            from .api.overview import resolve_entity
            from .api.responses import APIError, json_body, success_payload

            if not self._api_mutation_allowed():
                self._send_api_error("FORBIDDEN", "无权执行该操作", 403)
                return
            incident_match = re.fullmatch(r"/api/v1/incidents/([0-9A-Za-z_-]+)/transition", u.path)
            review_match = (None if u.path == "/api/v1/reviews/batch" else
                            re.fullmatch(r"/api/v1/reviews/([0-9A-Za-z_-]+)", u.path))
            if u.path not in {
                "/api/v1/collection/run", "/api/v1/collection/stop", "/api/v1/reviews/batch",
                "/api/v1/collection/login/open", "/api/v1/reports/generate",
                "/api/v1/keywords", "/api/v1/seeds",
            } and incident_match is None and review_match is None:
                self._send_api_error("NOT_FOUND", "接口不存在", 404)
                return
            try:
                from . import load_watch
                watch = load_watch()
                entity_id, _ = resolve_entity(watch, None)
            except APIError as exc:
                self._send_api_error(exc.code, exc.message, exc.status)
                return
            except (SystemExit, OSError, ValueError, TypeError):
                self._send_api_error("CONFIG_ERROR", "监控配置无法读取", 503)
                return

            if review_match is not None or u.path == "/api/v1/reviews/batch":
                from .api.reviews import save_review, save_review_batch
                try:
                    body = json_body(self)
                    requested_entity = str(body.get("entity_id") or "").strip() or None
                    resolved_entity_id, _ = resolve_entity(watch, requested_entity)
                    principal = self._api_principal() or {}
                    actor = principal.get("name") or principal.get("open_id") or "unknown"
                    store = Store(db)
                    try:
                        if review_match is not None:
                            review = save_review(
                                store, watch, review_match.group(1),
                                verdict=str(body.get("verdict") or "").strip(),
                                entity_id=requested_entity,
                                note=str(body.get("note") or ""),
                                actor=actor,
                            )
                            data = {"review": review}
                        else:
                            data = save_review_batch(
                                store, watch, body.get("items"),
                                entity_id=requested_entity, actor=actor,
                            )
                    finally:
                        store.close()
                except APIError as exc:
                    self._send_api_error(exc.code, exc.message, exc.status)
                    return
                except Exception:
                    self._send_api_error("INTERNAL_ERROR", "服务暂时不可用", 500)
                    return
                self._send_json(success_payload(
                    data, entity_id=resolved_entity_id, data_quality="ok",
                ))
                return

            if incident_match is not None:
                from . import alerts
                from .api.incidents import allowed_action_names, serialize_incident
                try:
                    body = json_body(self)
                    action = str(body.get("action") or "").strip()
                    note = str(body.get("note") or "").strip()[:1000]
                    store = Store(db)
                    try:
                        incident = store.get_incident(incident_match.group(1))
                        if incident is None:
                            raise APIError("NOT_FOUND", "事件不存在", 404)
                        if action not in allowed_action_names(incident):
                            raise APIError("INVALID_TRANSITION", "当前状态不能执行该操作", 409)
                        principal = self._api_principal() or {}
                        actor = principal.get("name") or principal.get("open_id") or "unknown"
                        now = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
                        result = alerts.transition(
                            store, incident["incident_id"], action, actor=actor, now=now, note=note,
                        )
                        if not result.get("success"):
                            code = "DELIVERY_FAILED" if action == "escalate" else "INVALID_TRANSITION"
                            raise APIError(code, result.get("message") or "事件状态更新失败", 409)
                        data = {
                            "incident": serialize_incident(result["incident"]),
                            "executive_pushed": bool(result.get("executive_pushed")),
                        }
                    finally:
                        store.close()
                except APIError as exc:
                    self._send_api_error(exc.code, exc.message, exc.status)
                    return
                except Exception:
                    self._send_api_error("INTERNAL_ERROR", "服务暂时不可用", 500)
                    return
                self._send_json(success_payload(data, entity_id=entity_id, data_quality="ok"))
                return
            if u.path == "/api/v1/collection/login/open":
                from . import login
                try:
                    platform = str(json_body(self).get("platform") or "").strip()
                    if platform not in login.LOGIN_URLS:
                        raise APIError("INVALID_PARAMETER", "该平台不支持交互登录", 400)
                    message = login.open_login(platform)
                except APIError as exc:
                    self._send_api_error(exc.code, exc.message, exc.status)
                    return
                except Exception as exc:
                    self._send_api_error("COLLECTOR_UNAVAILABLE", str(exc)[:200], 409)
                    return
                self._send_json(success_payload(
                    {"platform": platform, "message": message or "已打开登录页"},
                    entity_id=entity_id, data_quality="ok",
                ))
                return
            if u.path in {"/api/v1/keywords", "/api/v1/seeds"}:
                from .api.watch import mutate_keyword, mutate_seed
                try:
                    body = json_body(self)
                    requested_entity = str(body.get("entity_id") or "").strip() or None
                    resolved_entity_id, _ = resolve_entity(watch, requested_entity)
                    store = Store(db)
                    try:
                        if u.path == "/api/v1/keywords":
                            result = mutate_keyword(
                                store, watch, body, entity_id=requested_entity,
                            )
                        else:
                            result, watch = mutate_seed(
                                store, watch, body, entity_id=requested_entity,
                            )
                    finally:
                        store.close()
                except APIError as exc:
                    self._send_api_error(exc.code, exc.message, exc.status)
                    return
                except Exception:
                    self._send_api_error("INTERNAL_ERROR", "监控配置操作失败", 500)
                    return
                self._send_json(success_payload(
                    {"entity": {"id": resolved_entity_id}, "result": result},
                    entity_id=resolved_entity_id, data_quality="ok",
                ))
                return
            if u.path == "/api/v1/reports/generate":
                from .api.reports import generate_report
                try:
                    body = json_body(self)
                    requested_entity = str(body.get("entity_id") or "").strip() or None
                    store = Store(db)
                    try:
                        data, quality, quality_notes = generate_report(
                            store, watch, entity_id=requested_entity,
                        )
                    finally:
                        store.close()
                except APIError as exc:
                    self._send_api_error(exc.code, exc.message, exc.status)
                    return
                except Exception:
                    self._send_api_error("INTERNAL_ERROR", "报告生成失败", 500)
                    return
                self._send_json(success_payload(
                    data, entity_id=data["entity"]["id"], data_quality=quality,
                    quality_notes=quality_notes,
                ), 201)
                return
            if u.path == "/api/v1/collection/run":
                execution = execution_environment()
                if not execution["can_run"]:
                    self._send_api_error("COLLECTION_UNAVAILABLE", execution["message"], 409)
                    return
                result = _start_background_run(db)
            else:
                result = _request_run_stop()
                if not result["stop_requested"]:
                    self._send_api_error("NOT_RUNNING", result["message"], 409)
                    return
            self._send_json(success_payload(result, entity_id=entity_id, data_quality="ok"))

        def _handle_api_v1_put(self, u) -> None:
            from .api.responses import APIError, json_body, success_payload

            if u.path != "/api/v1/watch":
                self._send_api_error("NOT_FOUND", "接口不存在", 404)
                return
            if not self._api_mutation_allowed():
                self._send_api_error("FORBIDDEN", "无权执行该操作", 403)
                return
            try:
                from . import load_watch
                from .api.overview import resolve_entity
                from .api.watch import build_watch_config, update_watch_config
                body = json_body(self)
                current = load_watch()
                updated = update_watch_config(current, body)
                requested_entity = str(body.get("entity_id") or "").strip() or None
                entity_id, _ = resolve_entity(updated, requested_entity)
                data = build_watch_config(updated, entity_id=entity_id)
            except APIError as exc:
                self._send_api_error(exc.code, exc.message, exc.status)
                return
            except Exception:
                self._send_api_error("CONFIG_WRITE_FAILED", "监控配置保存失败", 500)
                return
            self._send_json(success_payload(data, entity_id=entity_id, data_quality="ok"))

        def _redirect(self, location: str, set_cookie: str = ""):
            """302 跳转，可带 Set-Cookie（登录建会话/登出清会话）。"""
            self.send_response(302)
            self.send_header("Location", location)
            if set_cookie:
                self.send_header("Set-Cookie", set_cookie)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _auth_login(self):
            """/auth/login：生成 state → 302 跳飞书授权页。未配置 App 则友好提示（不 500）。"""
            from . import config
            u = urlparse(self.path)
            nxt = parse_qs(u.query).get("next", ["/"])[0]
            app_id = config.resolve("FEISHU_APP_ID")
            redirect_uri = config.resolve("FEISHU_REDIRECT_URI")
            if not app_id or not redirect_uri:          # DoD#3：缺配置给提示页，不报 500
                self._send(render_auth_hint()); return
            state = _new_state(nxt)
            self._redirect(_feishu_authorize_url(app_id, redirect_uri, state))

        def _auth_callback(self):
            """/auth/callback：校验 state → code 换 token → 取用户信息 → 建会话 → 302 回原始路径。"""
            q = parse_qs(urlparse(self.path).query)
            code = q.get("code", [""])[0]
            state = q.get("state", [""])[0]
            st = _oauth_states.pop(state, None)         # state 一次性消费（防 CSRF/重放）
            if not code or not st:
                self._send(render_auth_error("授权校验失败（state 无效或已过期），请重新登录。"), 400)
                return
            try:
                token = _feishu_user_access_token(code)
                user = _feishu_user_info(token)
            except Exception as e:                      # 飞书 API 异常也不 500，给可读提示
                self._send(render_auth_error(f"飞书登录失败：{str(e)[:200]}"), 502); return
            sid = _new_session(user)
            self._redirect(_safe_next(st.get("next", "/")), _cookie_header(sid))

        def _auth_logout(self):
            """/auth/logout：清会话 + 过期 cookie → 302 回登录页。"""
            sid = _sid_from_cookie(self)
            if sid:
                _session_delete(sid)
            self._redirect("/auth/login", _cookie_header("", clear=True))

        def do_GET(self):
            u = urlparse(self.path)
            # /auth/* 是登录流程页，本身无需登录
            if u.path == "/auth/login":
                self._auth_login(); return
            if u.path == "/auth/callback":
                self._auth_callback(); return
            if u.path == "/auth/logout":
                self._auth_logout(); return
            # /config 维持原有本机保护（SSH 隧道用），不走飞书 OAuth
            if u.path == "/config":
                if not _write_allowed(self):
                    self.send_error(403); return
                self._send(render_config()); return
            if u.path == "/config/test":
                if not _write_allowed(self):
                    self.send_error(403); return
                self._send(render_config(test_msg=_run_test(parse_qs(u.query).get("p", [""])[0]))); return
            if u.path.startswith("/api/v1/"):
                self._handle_api_v1_get(u); return
            if u.path in ("/", "/v2", "/v2/"):
                if not _write_allowed(self) and _require_auth(self) is None:
                    self._redirect(f"/auth/login?next={quote(self.path, safe='')}"); return
                self._send_workbench_asset("index.html")
                return
            asset_prefix = next(
                (prefix for prefix in ("/assets/", "/v2/assets/") if u.path.startswith(prefix)),
                None,
            )
            if asset_prefix is not None:
                if self._api_principal() is None:
                    self.send_error(401); return
                self._send_workbench_asset(u.path[len(asset_prefix):])
                return
            if u.path == "/api/run/status":
                if not (_write_allowed(self) or _require_auth(self)):
                    self.send_error(403); return
                payload = json.dumps({"running": _run_state["running"], "last": _run_state["last"],
                                      "current": _run_state["current"]},
                                     ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload); return
            if u.path == "/api/login/status":
                if not _write_allowed(self):
                    self.send_error(403); return
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
                if not _write_allowed(self):
                    self.send_error(403); return
                self._send(render_login()); return
            if u.path == "/watch":
                if not _write_allowed(self):
                    self.send_error(403); return
                self._send(render_watch()); return
            # 本机保持零配置可用；远程访问必须飞书 OAuth 登录。
            if not _write_allowed(self) and _require_auth(self) is None:
                self._redirect(f"/auth/login?next={quote(self.path, safe='')}"); return
            store = Store(db)
            try:
                if u.path == "/legacy":
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
                elif u.path == "/annotate":
                    body = render_annotate(store, parse_qs(u.query))
                elif u.path == "/accounts":
                    body = render_accounts(store)
                elif u.path == "/api/seed/list":
                    from .keywords import KeywordManager
                    km = KeywordManager(store)
                    entity_id = parse_qs(u.query).get("entity", [None])[0]
                    seeds = km.list_suggestions(status='pending', entity_id=entity_id, tag='seed_alias')
                    payload = json.dumps({"seeds": seeds}, ensure_ascii=False, default=str).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload); return
                elif u.path == "/api/annotate/queue":
                    from . import analytics
                    entity_id = parse_qs(u.query).get("entity", [None])[0]
                    queue = analytics.active_sample(store, entity_id, limit=20)
                    payload = json.dumps({"queue": queue}, ensure_ascii=False, default=str).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload); return
                elif u.path == "/api/keywords":
                    # API: 返回JSON
                    from .keywords import KeywordManager
                    km = KeywordManager(store)
                    tag = parse_qs(u.query).get("tag", [None])[0]
                    entity_id = parse_qs(u.query).get("entity", [None])[0]
                    keywords = km.list(tag=tag, entity_id=entity_id)
                    suggestions = km.list_suggestions(status='pending', entity_id=entity_id, exclude_tag='seed_alias')
                    payload = json.dumps({
                        'keywords': keywords,
                        'suggestions': suggestions
                    }, ensure_ascii=False, default=str).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload); return
                elif u.path == "/api/incidents":
                    status = parse_qs(u.query).get("status", [None])[0]
                    payload = json.dumps({"incidents": store.list_incidents(status=status)},
                                         ensure_ascii=False, default=str).encode("utf-8")
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
            if u.path.startswith("/api/v1/"):
                self._handle_api_v1_post(u); return
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
                if not _mutation_allowed(self):
                    self.send_error(403); return
                result = _start_background_run(db)
                payload = json.dumps(result, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            elif u.path == "/api/run/stop":
                # 协作式停止：置标志，采集在下个平台边界中止（已采数据保留）
                if not _mutation_allowed(self):
                    self.send_error(403); return
                stop_result = _request_run_stop()
                result = {"success": stop_result["stop_requested"], "message": stop_result["message"]}
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
                if not _mutation_allowed(self):
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

            elif u.path == "/api/annotate":
                # 多维标注落库 + 圈词进关键词库待审（product_name 额外进种子建议，扩召回）
                if not _mutation_allowed(self):
                    self.send_error(403); return
                import datetime as _dt2
                store = Store(db)
                try:
                    from .keywords import KeywordManager, SUBJECTS, STANCES
                    n = int(self.headers.get("Content-Length") or 0)
                    d = json.loads(self.rfile.read(n).decode("utf-8"))
                    doc_id = d.get("doc_id")
                    subject = d.get("subject") if d.get("subject") in SUBJECTS else None
                    stance = d.get("stance") if d.get("stance") in STANCES else None
                    if not doc_id or not subject or not stance:
                        result = {"success": False, "message": "缺 doc_id/主体/立场，或枚举非法"}
                    else:
                        eid = d.get("entity_id")
                        words = d.get("picked_words") or []
                        now = _dt2.datetime.now().isoformat(timespec="seconds")
                        store.add_annotation(doc_id, subject=subject, stance=stance,
                                             importance=d.get("importance"), picked_words=words,
                                             note=d.get("note", ""), sample_source=d.get("sample_source", "manual"),
                                             entity_id=eid, ts=now)
                        km = KeywordManager(store)
                        for w in words:
                            word, role = (w.get("word") or "").strip(), w.get("role") or "related"
                            if not word:
                                continue
                            try:                                       # 圈词进判别词待审队列
                                km.add_suggestion(word, role, eid, score=0.9, reason="标注圈选",
                                                  source_docs=json.dumps([doc_id]))
                                if role == "product_name":              # 产品名额外进种子建议（扩召回侧）
                                    km.add_suggestion(word, "seed_alias", eid, score=0.9,
                                                      reason="标注圈选·产品名", source_docs=json.dumps([doc_id]))
                            except Exception:
                                pass                                    # 重复/异常不阻断标注保存
                        result = {"success": True, "message": "已保存"}
                    payload = json.dumps(result, ensure_ascii=False).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                finally:
                    store.close()
            elif u.path == "/api/seed":
                # 种子建议：mine 挖词 / approve 写回 watch.yaml aliases / reject
                if not _mutation_allowed(self):
                    self.send_error(403); return
                store = Store(db)
                try:
                    from .keywords import KeywordManager
                    from . import analytics, load_watch
                    n = int(self.headers.get("Content-Length") or 0)
                    d = json.loads(self.rfile.read(n).decode("utf-8"))
                    act = d.get("action")
                    km = KeywordManager(store)
                    if act == "mine":
                        try:
                            cnt = analytics.mine_and_queue(store, load_watch(), km=km)
                            result = {"success": True, "message": f"挖词完成：种子 {cnt['seed']} · 判别词 {cnt['feature']}"}
                        except Exception as e:
                            result = {"success": False, "message": f"挖词失败：{str(e)[:150]}"}
                    elif act == "approve":
                        sug = next((s for s in km.list_suggestions(status='pending', tag='seed_alias')
                                    if s["id"] == int(d.get("id", 0))), None)
                        if not sug:
                            result = {"success": False, "message": "建议不存在"}
                        else:
                            ok, msg = analytics.append_alias(sug["entity_id"], sug["word"])
                            if ok:
                                km.mark_suggestion(sug["id"], "approved")
                            result = {"success": ok, "message": msg}
                    elif act == "reject":
                        ok = km.reject_suggestion(int(d.get("id", 0)))
                        result = {"success": ok, "message": "已忽略" if ok else "失败"}
                    else:
                        result = {"success": False, "message": "未知操作"}
                    payload = json.dumps(result, ensure_ascii=False).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                finally:
                    store.close()
            elif u.path == "/api/accounts":
                # 官方账号白名单：add / delete
                if not _mutation_allowed(self):
                    self.send_error(403); return
                import datetime as _dt3
                store = Store(db)
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                    d = json.loads(self.rfile.read(n).decode("utf-8"))
                    act = d.get("action")
                    if act == "add" and (d.get("author") or "").strip() and d.get("subject_type") in ("官方", "准官方", "媒体"):
                        store.add_account(d["author"].strip(), d["subject_type"],
                                          platform=(d.get("platform") or "").strip(),
                                          note="", ts=_dt3.datetime.now().isoformat(timespec="seconds"))
                        result = {"success": True, "message": "已添加"}
                    elif act == "delete":
                        result = {"success": store.delete_account(int(d.get("id", 0))), "message": "已删除"}
                    else:
                        result = {"success": False, "message": "参数不合法（账号必填，类型须官方/准官方/媒体）"}
                    payload = json.dumps(result, ensure_ascii=False).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                finally:
                    store.close()
            elif u.path == "/api/incidents":
                if not _mutation_allowed(self):
                    self.send_error(403); return
                store = Store(db)
                try:
                    from . import alerts as _alerts
                    n = int(self.headers.get("Content-Length") or 0)
                    d = json.loads(self.rfile.read(n).decode("utf-8"))
                    user = _require_auth(self) or {}
                    actor = user.get("name") or user.get("open_id") or "local"
                    result = _alerts.transition(
                        store, d.get("incident_id", ""), d.get("action", ""), actor=actor,
                        note=d.get("note", ""), now=_dt.datetime.now().astimezone().isoformat(timespec="seconds"))
                    payload = json.dumps(result, ensure_ascii=False, default=str).encode("utf-8")
                    self.send_response(200 if result.get("success") else 400)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                finally:
                    store.close()
            else:
                self.send_error(404)

        def do_PUT(self):
            u = urlparse(self.path)
            if u.path.startswith("/api/v1/"):
                self._handle_api_v1_put(u); return
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
    global _SESSION_DB
    _SESSION_DB = db
    _session_init(db)
    print(f"看板已启动（只读）：http://{host}:{port}  （Ctrl+C 停止）")
    # ThreadingHTTPServer: 每请求独立线程，慢接口（heimao 浏览器探测/跑批）不阻塞整站。
    # 每请求自建/关 SQLite 连接（见 do_GET），不跨线程共享，故线程安全。
    ThreadingHTTPServer((host, port), make_handler(db)).serve_forever()


if __name__ == "__main__":
    import sys
    serve(sys.argv[1] if len(sys.argv) > 1 else "yuqing.db")
