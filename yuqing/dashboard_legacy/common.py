# -*- coding: utf-8 -*-
"""Shared HTML shell and escaping helpers for legacy dashboard pages."""

from __future__ import annotations

import html
import re

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
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")


def _safe_href(url: str) -> str:
    """Allow only http(s)/relative hrefs, then escape attribute delimiters."""
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


def _inline(s: str) -> str:
    """Render the supported inline Markdown subset after HTML escaping."""
    s = html.escape(s)

    def _lk(match):
        text, url = match.group(1), match.group(2)
        if url.startswith(("http://", "https://", "/")):
            return f"<a href='{url}' target=_blank rel=noopener>{text}</a>"
        return text

    s = _LINK.sub(_lk, s)
    return _BOLD.sub(r"<strong>\1</strong>", s)


def _md_table(rows: list) -> str:
    def cells(row):
        return [cell.strip() for cell in row.strip().strip("|").split("|")]

    def is_sep(row):
        return set(row.replace("|", "").strip()) <= set("-: ") and "-" in row

    header = cells(rows[0])
    th = "".join(f"<th>{_inline(cell)}</th>" for cell in header)
    body = "".join(
        "<tr>" + "".join(f"<td>{_inline(cell)}</td>" for cell in cells(row)) + "</tr>"
        for row in rows[1:] if not is_sep(row)
    )
    return f"<table><tr>{th}</tr>{body}</table>"


def md_to_html(md: str) -> str:
    """Render the fixed Markdown subset emitted by this project."""
    lines, out, index = md.split("\n"), [], 0
    while index < len(lines):
        stripped = lines[index].strip()
        if not stripped:
            index += 1
            continue
        if stripped.startswith("## "):
            out.append(f"<h2>{_inline(stripped[3:])}</h2>")
        elif stripped.startswith("# "):
            out.append(f"<h1>{_inline(stripped[2:])}</h1>")
        elif stripped.startswith(">"):
            out.append(f"<blockquote>{_inline(stripped.lstrip('> '))}</blockquote>")
        elif stripped.startswith("---"):
            out.append("<hr>")
        elif stripped.startswith("|"):
            block = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                block.append(lines[index].strip())
                index += 1
            out.append(_md_table(block))
            continue
        else:
            out.append(f"<p>{_inline(stripped)}</p>")
        index += 1
    return "\n".join(out)
