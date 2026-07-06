# -*- coding: utf-8 -*-
"""最小只读看板：报告历史 + 采集健康三态 + 负面 Top。

ponytail: 只读、无交互 → 用 stdlib http.server 直读 SQLite，零新依赖
（不上 Streamlit/FastAPI）。渲染是纯函数 render_index/render_report，可离线自检。
    python -m yuqing.dashboard yuqing.db      # 起服务，浏览器开 http://127.0.0.1:8000
只读：仅 GET，绝不改库。
"""

from __future__ import annotations

import html
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
        f"<td><a href='{html.escape(r['url'] or '#')}' target=_blank rel=noopener>原帖</a> "
        f"<span class=muted>{html.escape(r['doc_id'])}</span></td></tr>"
        for i, r in enumerate(conn.execute(
            "SELECT c.platform,c.text,c.url,c.doc_id,f.risk FROM clean c JOIN features f USING(doc_id)"
            " WHERE f.polarity='neg' ORDER BY f.risk DESC LIMIT 20"), 1)
    ) or "<tr><td colspan=5 class=muted>暂无负面</td></tr>"

    body = (
        "<h1>舆情监控看板 <span class=muted>（只读）</span></h1>" + banner + review_line +
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


def render_report(store: Store, run_id: str) -> str:
    row = store.conn.execute("SELECT markdown FROM reports WHERE run_id=?", (run_id,)).fetchone()
    if not row:
        return _page("未找到", "<p>未找到该报告。<a href='/'>返回</a></p>")
    # ponytail: 只读内部页，原始 markdown 用 <pre> 展示够用；富渲染要 markdown 库，不值当
    return _page(run_id, f"<p><a href='/'>← 返回</a></p><pre>{html.escape(row['markdown'])}</pre>")


def make_handler(db: str):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            u = urlparse(self.path)
            store = Store(db)
            try:
                if u.path == "/":
                    body = render_index(store)
                elif u.path == "/report":
                    rid = parse_qs(u.query).get("run_id", [""])[0]
                    body = render_report(store, rid)
                else:
                    self.send_error(404); return
            finally:
                store.close()
            data = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):  # 静音默认日志
            pass
    return Handler


def serve(db: str = "yuqing.db", host: str = "127.0.0.1", port: int = 8000) -> None:
    print(f"看板已启动（只读）：http://{host}:{port}  （Ctrl+C 停止）")
    HTTPServer((host, port), make_handler(db)).serve_forever()


if __name__ == "__main__":
    import sys
    serve(sys.argv[1] if len(sys.argv) > 1 else "yuqing.db")
