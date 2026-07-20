# -*- coding: utf-8 -*-
"""Single-process background run coordination for the dashboard."""

from __future__ import annotations

import contextlib
import datetime as _dt
import io
import threading


_run_lock = threading.Lock()
_run_state = {"running": False, "last": None, "current": "", "stop": False}

_PLATFORM_CN = {
    "weibo": "微博", "zhihu": "知乎", "xiaohongshu": "小红书", "douyin": "抖音",
    "bilibili": "B站", "tieba": "贴吧", "hupu": "虎扑", "smzdm": "值得买",
    "weixin": "公众号", "heimao": "黑猫投诉",
}
_STAGE_CN = {
    "_analyze": "正在分析情感/方面…",
    "_embed": "正在语义向量化…",
    "_report": "正在生成报告…",
}


def _progress(entity_id, platform) -> None:
    if platform in _STAGE_CN:
        _run_state["current"] = _STAGE_CN[platform]
    else:
        _run_state["current"] = f"正在采集{_PLATFORM_CN.get(platform, platform)}数据…"


def _do_run(db: str) -> None:
    buffer = io.StringIO()
    ok, message = False, ""
    _run_state["current"] = "正在启动…"
    try:
        from .run import main as run_main
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            code = run_main(
                db=db,
                on_progress=_progress,
                should_stop=lambda: _run_state["stop"],
            )
        stopped = _run_state["stop"]
        ok = (code == 0) and not stopped
        output = buffer.getvalue().strip()
        tail = output.splitlines()[-1] if output else f"退出码 {code}"
        message = ("已停止（部分数据已入库）｜" + tail) if stopped else tail
    except SystemExit as exc:
        message = f"配置/依赖错误：{exc}"
    except Exception as exc:
        message = f"运行异常：{str(exc)[:200]}"
    finally:
        _run_state["last"] = {
            "ok": ok,
            "msg": message,
            "at": _dt.datetime.now().strftime("%H:%M:%S"),
        }
        _run_state["current"] = ""
        _run_state["stop"] = False
        _run_state["running"] = False


def _start_background_run(db: str) -> dict:
    """Start at most one background run and return a stable state shape."""
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
            return {
                "running": False,
                "stop_requested": False,
                "message": "当前无运行中的采集",
            }
        _run_state["stop"] = True
        _run_state["current"] = "正在停止…（完成当前平台后中止）"
        return {"running": True, "stop_requested": True, "message": "已请求停止"}
