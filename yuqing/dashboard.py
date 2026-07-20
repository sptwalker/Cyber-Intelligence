# -*- coding: utf-8 -*-
"""最小看板：报告历史 + 采集健康三态 + 负面 Top + 系统配置页。

ponytail: 数据视图只读 → stdlib http.server 直读 SQLite，零新依赖。/config 是唯一写入口
（表单存 yuqing_config.json），仅限本机（127.0.0.1 绑定 + Host 头校验）。
    python -m yuqing.dashboard yuqing.db      # 起服务，浏览器开 http://127.0.0.1:8000
"""

from __future__ import annotations

import html
import json
import threading
import secrets
import time
import urllib.request
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode, urlparse

from .store import Store
from .watch_config import validate_watch as _validate_watch
from .dashboard_runtime import (
    _PLATFORM_CN,
    _STAGE_CN,
    _do_run,
    _progress,
    _request_run_stop,
    _run_lock,
    _run_state,
    _start_background_run,
)

_WORKBENCH_DIR = Path(__file__).parent / "web" / "workbench"

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

from .dashboard_views import (
    _BOLD,
    _CSS,
    _LINK,
    _STATE_CN,
    _badge,
    _inline,
    _md_table,
    _page,
    _safe_href,
    _self_entities,
    chart_data,
    md_to_html,
    render_accounts,
    render_annotate,
    render_config,
    render_dash,
    render_exec,
    render_index,
    render_keywords,
    render_login,
    render_report,
    render_watch,
)

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
    """Build the compatibility HTTP handler around the split dashboard modules."""
    from . import dashboard as app
    from .dashboard_http import make_handler as build_handler
    return build_handler(db, app)


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
