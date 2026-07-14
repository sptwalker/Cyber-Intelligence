# -*- coding: utf-8 -*-
"""本地人工登录辅助：看各平台登录态 + 一键打开登录页。

采集靠 opencli 复用真实 Chrome 的登录会话（见 collect.py）。登录本身要人工过扫码/短信/
风控，无法全自动——本模块只把"看状态、开登录页、重新检测"做顺手，登录后跑批见 run/scheduler。

- status(): adapter 平台走 `opencli auth status`（快）；heimao 无 adapter，走浏览器桥探登录墙。
- open_login(): 在桥接的 Chrome 里打开登录页，用户自己登。
- bridge_ok(): opencli doctor 看 extension 是否连着。
所有 subprocess 带 timeout + 兜底，绝不抛（失败落 error 字段）。
"""

from __future__ import annotations

import json
import subprocess

from .collect import _OPENCLI, _opencli_browser, _heimao_is_login_wall
import os

# 平台 → 登录/首页 URL（免登录平台 tieba/hupu/smzdm/weixin 不列，无需登录）
LOGIN_URLS = {
    "weibo": "https://weibo.com/login.php",
    "zhihu": "https://www.zhihu.com/signin",
    "xiaohongshu": "https://www.xiaohongshu.com",
    "douyin": "https://www.douyin.com",
    "bilibili": "https://passport.bilibili.com/login",
    "heimao": "https://tousu.sina.com.cn/",     # 黑猫用微博账号登录
}

# 有 opencli auth adapter 的平台（heimao 除外，它走浏览器桥探测）
AUTH_SITES = {"weibo", "zhihu", "xiaohongshu", "douyin", "bilibili"}


def _session() -> str:
    return os.getenv("YUQING_OPENCLI_SESSION", "yuqing")


def bridge_ok() -> tuple[bool, str]:
    """opencli 浏览器桥是否连着（extension connected）。返回 (ok, 说明)。"""
    from . import collector_client
    if collector_client.enabled():
        return collector_client.bridge_status()
    try:
        out = subprocess.run([_OPENCLI, "doctor"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=30)
        txt = (out.stdout or "") + (out.stderr or "")
        ok = "Extension: connected" in txt
        return ok, "浏览器桥已连接" if ok else "浏览器桥未连接（请确认 Chrome 装了 opencli 扩展并运行）"
    except Exception as e:
        return False, f"检测桥失败：{str(e)[:120]}"


def _auth_status(sites: list[str]) -> dict[str, dict]:
    """一次批量查 adapter 平台登录态。返回 {site: {logged_in, identity}}。失败返回空。"""
    if not sites:
        return {}
    from . import collector_client
    if collector_client.enabled():
        try:
            rows = collector_client.login_status(sites)
            return {
                row["platform"]: {
                    "logged_in": bool(row.get("logged_in")),
                    "identity": row.get("identity") or "",
                }
                for row in rows if row.get("platform") in sites
            }
        except Exception:
            return {}
    try:
        out = subprocess.run(
            [_OPENCLI, "auth", "status", "--site", ",".join(sites), "--format", "json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        rows = json.loads(out.stdout or "[]")
        return {r["site"]: {"logged_in": bool(r.get("logged_in")),
                            "identity": r.get("identity") or ""} for r in rows}
    except Exception:
        return {}


def _heimao_logged_in() -> tuple[bool, str]:
    """浏览器桥打开黑猫搜索页，看是否登录墙。慢（~6s）。返回 (logged_in, error)。"""
    from . import collector_client
    if collector_client.enabled():
        try:
            row = next((
                item for item in collector_client.login_status(["heimao"])
                if item.get("platform") == "heimao"
            ), None)
            if row is None:
                return False, "Collector 未返回黑猫登录状态"
            return bool(row.get("logged_in")), str(row.get("error") or "")
        except Exception as exc:
            return False, str(exc)[:120]
    try:
        s = _session()
        _opencli_browser(s, "open", "https://tousu.sina.com.cn/index/search/?keywords=test")
        md = _opencli_browser(s, "extract")
        return (not _heimao_is_login_wall(md)), ""
    except Exception as e:
        return False, str(e)[:120]


def status(platforms: list[str]) -> list[dict]:
    """各平台登录态。platforms 里只有 LOGIN_URLS 覆盖的才检测（其余免登录，跳过）。

    返回 [{platform, logged_in, identity, method, error}]，顺序同 LOGIN_URLS。
    """
    want = [p for p in LOGIN_URLS if p in platforms]
    auth = _auth_status([p for p in want if p in AUTH_SITES])
    result = []
    for p in want:
        if p == "heimao":
            ok, err = _heimao_logged_in()
            result.append({"platform": p, "logged_in": ok, "identity": "",
                           "method": "browser", "error": err})
        else:
            a = auth.get(p)
            if a is None:
                result.append({"platform": p, "logged_in": False, "identity": "",
                               "method": "auth", "error": "查询失败/无 adapter"})
            else:
                result.append({"platform": p, "logged_in": a["logged_in"],
                               "identity": a["identity"], "method": "auth", "error": ""})
    return result


def open_login(platform: str) -> str:
    """在桥接的 Chrome 里打开平台登录页。platform 必须是 LOGIN_URLS 的 key（防注入）。"""
    if platform not in LOGIN_URLS:
        raise ValueError(f"未知平台：{platform}")
    from . import collector_client
    if collector_client.enabled():
        return collector_client.open_login(platform)
    _opencli_browser(_session(), "open", LOGIN_URLS[platform])
    return f"已在浏览器打开 {platform} 登录页"


def _selfcheck() -> None:
    """离线自检：mock subprocess 断言解析逻辑（不触网）。"""
    import yuqing.login as L

    # 1) auth status 解析：logged_in 真/假
    class _R:
        def __init__(self, o): self.stdout, self.stderr, self.returncode = o, "", 0
    orig_run = subprocess.run
    subprocess.run = lambda *a, **k: _R(json.dumps([
        {"site": "weibo", "logged_in": True, "identity": "me"},
        {"site": "zhihu", "logged_in": False, "identity": ""}]))
    got = _auth_status(["weibo", "zhihu"])
    assert got["weibo"]["logged_in"] is True and got["weibo"]["identity"] == "me"
    assert got["zhihu"]["logged_in"] is False

    # 2) bridge_ok：connected / 断
    subprocess.run = lambda *a, **k: _R("[OK] Extension: connected (v1)")
    assert bridge_ok()[0] is True
    subprocess.run = lambda *a, **k: _R("Extension: disconnected")
    assert bridge_ok()[0] is False
    subprocess.run = orig_run

    # 3) heimao 登录墙判断（复用 collect._heimao_is_login_wall）
    assert _heimao_is_login_wall("请直接登录\n[登录](javascript:;)") is True
    assert _heimao_is_login_wall("暂无投诉\n[退出](javascript:;)") is False

    # 4) open_login 白名单
    try:
        open_login("evil; rm -rf")
        assert False, "非白名单应拒绝"
    except ValueError:
        pass

    print("OK login: auth解析✓ 桥连通✓ heimao登录墙✓ 开登录页白名单✓")


if __name__ == "__main__":
    import sys
    from . import load_watch

    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        _selfcheck()
    else:
        ok, msg = bridge_ok()
        print(f"浏览器桥：{'✅' if ok else '❌'} {msg}\n")
        platforms = load_watch().get("platforms", [])
        print(f"{'平台':<14}{'登录态':<10}{'身份/备注'}")
        print("-" * 44)
        for r in status(platforms):
            mark = "✅已登录" if r["logged_in"] else "⬜未登录"
            extra = r["identity"] or r["error"] or ("浏览器探测" if r["method"] == "browser" else "")
            print(f"{r['platform']:<14}{mark:<10}{extra}")
