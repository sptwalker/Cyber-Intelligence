# -*- coding: utf-8 -*-
"""最小看板：报告历史 + 采集健康三态 + 负面 Top + 系统配置页。

ponytail: 数据视图只读 → stdlib http.server 直读 SQLite，零新依赖。/config 是唯一写入口
（表单存 yuqing_config.json），仅限本机（127.0.0.1 绑定 + Host 头校验）。
    python -m yuqing.dashboard yuqing.db      # 起服务，浏览器开 http://127.0.0.1:8000
"""

from __future__ import annotations

import html
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

from .store import Store

_CSS = """
body{font:14px/1.5 -apple-system,Segoe UI,Microsoft YaHei,sans-serif;max-width:1000px;margin:24px auto;padding:0 16px;color:#1f2328}
h1{font-size:20px} h2{font-size:16px;margin-top:28px;border-bottom:1px solid #d0d7de;padding-bottom:4px}
table{border-collapse:collapse;width:100%;margin:8px 0} th,td{border:1px solid #d0d7de;padding:6px 8px;text-align:left;vertical-align:top}
th{background:#f6f8fa} a{color:#0969da;text-decoration:none} a:hover{text-decoration:underline}
.badge{padding:1px 8px;border-radius:10px;color:#fff;font-size:12px;white-space:nowrap}
.ok{background:#1a7f37} .suspect{background:#9a6700} .fail{background:#cf222e}
.muted{color:#656d76;font-size:12px} pre{white-space:pre-wrap;background:#f6f8fa;padding:12px;border-radius:6px;overflow:auto}
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
        "<a href='/exec' style='font-size:14px'>📊 高管概览</a> "
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
            store = Store(db)
            try:
                if u.path == "/":
                    body = render_index(store)
                elif u.path == "/exec":
                    body = render_exec(store)
                elif u.path == "/report":
                    body = render_report(store, parse_qs(u.query).get("run_id", [""])[0])
                else:
                    self.send_error(404); return
            finally:
                store.close()
            self._send(body)

        def do_POST(self):
            if urlparse(self.path).path != "/config":
                self.send_error(404); return
            if not _write_allowed(self):                  # 写接口：仅本机 + 拒绝跨站(防CSRF)
                self.send_error(403); return
            from . import config
            n = int(self.headers.get("Content-Length") or 0)
            form = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode("utf-8")).items()}
            config.save(form)
            self._send(render_config(saved=True))

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
    HTTPServer((host, port), make_handler(db)).serve_forever()


if __name__ == "__main__":
    import sys
    serve(sys.argv[1] if len(sys.argv) > 1 else "yuqing.db")
