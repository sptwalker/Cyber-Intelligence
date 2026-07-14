# -*- coding: utf-8 -*-
"""Collection status read model and execution-environment description."""

from __future__ import annotations

import os
import shutil
import datetime as _dt
from typing import Any, Callable

from ..collect import _OPENCLI
from .overview import resolve_entity

_HEALTH_ORDER = {"unknown": -1, "ok": 0, "suspect": 1, "fail": 2}
_LOGIN_REQUIRED = {"weibo", "zhihu", "xiaohongshu", "douyin", "bilibili", "heimao"}


def execution_environment() -> dict[str, Any]:
    """Describe where collection would run without pretending a cloud pod owns Chrome."""
    in_kubernetes = bool(os.getenv("KUBERNETES_SERVICE_HOST"))
    configured = os.getenv("YUQING_ENABLE_COLLECTION")
    enabled = (not in_kubernetes) if configured is None else configured.lower() in {"1", "true", "yes", "on"}
    from .. import collector_client
    sidecar = collector_client.health() if collector_client.enabled() else None
    opencli_available = (
        bool(sidecar.get("opencli_available")) if sidecar is not None
        else bool(shutil.which(_OPENCLI))
    )
    collector_available = bool(sidecar.get("ready")) if sidecar is not None else opencli_available
    mode = os.getenv("YUQING_COLLECTION_EXECUTION_MODE") or (
        "kubernetes-dashboard" if in_kubernetes else "dashboard-process"
    )
    can_run = enabled and collector_available
    if can_run:
        message = (
            "采集由同一 Pod 内的独立 Collector sidecar 执行，业务服务负责入库和分析。"
            if sidecar is not None
            else "采集将在当前看板进程所在主机执行，并复用该主机的 opencli/Chrome 登录态。"
        )
    elif in_kubernetes and not enabled:
        message = "当前为云端看板环境；浏览器采集应在绑定 Chrome 的执行机运行。"
    elif sidecar is not None and not collector_available:
        message = str(sidecar.get("message") or "Collector sidecar 尚未就绪")
    elif not opencli_available:
        message = "当前主机未检测到 opencli，暂不能从工作台触发采集。"
    else:
        message = "当前环境已禁用工作台采集触发。"
    return {
        "mode": mode,
        "can_run": can_run,
        "opencli_available": opencli_available,
        "collector_available": collector_available,
        "in_kubernetes": in_kubernetes,
        "message": message,
    }


def latest_platform_runs(
    store, entity_id: str, platforms: list[str], *, now: _dt.datetime | None = None,
) -> tuple[list[dict], str, list[str]]:
    """Return the latest run per platform, aggregating aliases within the same run."""
    rows = store.conn.execute(
        "SELECT run_id,platform,entity_id,health,status,n_fetched,ts,note FROM run_log "
        "WHERE entity_id=? ORDER BY ts DESC", (entity_id,),
    ).fetchall()
    selected_run: dict[str, str] = {}
    latest: dict[str, dict] = {}
    for row in rows:
        platform = row["platform"]
        run_id = row["run_id"]
        selected_run.setdefault(platform, run_id)
        if selected_run[platform] != run_id:
            continue
        item = latest.setdefault(platform, {
            "run_id": run_id,
            "platform": platform,
            "entity_id": row["entity_id"],
            "health": "ok",
            "status": "ok",
            "n_fetched": 0,
            "ts": row["ts"],
            "note": "",
        })
        item["n_fetched"] += row["n_fetched"] or 0
        if _HEALTH_ORDER.get(row["health"], 2) > _HEALTH_ORDER.get(item["health"], 0):
            item["health"] = row["health"]
        if row["status"] != "ok":
            item["status"] = row["status"] or "error"
        if row["note"] and row["note"] not in item["note"]:
            item["note"] = "；".join(filter(None, (item["note"], row["note"])))

    expected = list(dict.fromkeys(platforms or latest.keys()))
    output = []
    missing = []
    degraded = []
    stale = []
    current = now or _dt.datetime.now().astimezone()
    try:
        stale_hours = max(1, int(os.getenv("YUQING_COLLECTION_STALE_HOURS", "24")))
    except ValueError:
        stale_hours = 24
    for platform in expected:
        item = latest.get(platform)
        if item is None:
            missing.append(platform)
            output.append({
                "run_id": None, "platform": platform, "entity_id": entity_id,
                "health": "unknown", "status": "unknown", "n_fetched": None,
                "ts": None, "note": "尚无采集记录",
            })
            continue
        output.append(item)
        try:
            observed = _dt.datetime.fromisoformat(str(item["ts"]))
            if observed.tzinfo is None:
                observed = observed.replace(tzinfo=current.tzinfo)
            age_hours = max(0.0, (current - observed.astimezone(current.tzinfo)).total_seconds() / 3600)
        except (TypeError, ValueError):
            age_hours = None
        item["age_hours"] = round(age_hours, 1) if age_hours is not None else None
        item["stale"] = age_hours is None or age_hours > stale_hours
        if item["stale"]:
            item["health"] = "suspect" if item["health"] == "ok" else item["health"]
            item["status"] = "stale" if item["status"] == "ok" else item["status"]
            item["note"] = "；".join(filter(None, (item["note"], f"采集记录超过 {stale_hours} 小时未更新")))
            stale.append(platform)
        if item["health"] != "ok" or item["status"] != "ok":
            degraded.append(f"{platform}({item['health']})")

    notes = []
    if not rows:
        notes.append("尚无采集运行记录，不能把空数据解释为零风险。")
        return output, "unknown", notes
    if missing:
        notes.append("平台尚无采集记录：" + "、".join(missing))
    if stale:
        notes.append("平台采集记录已过期：" + "、".join(stale))
    if degraded:
        notes.append("平台采集状态异常：" + "、".join(degraded))
    return output, ("degraded" if missing or degraded else "ok"), notes


def build_collection_status(
    store,
    watch: dict,
    run_state: dict,
    *,
    entity_id: str | None = None,
    login_provider: Callable[[list[str]], tuple[tuple[bool, str], list[dict]]] | None = None,
) -> tuple[dict[str, Any], str, list[str]]:
    """Combine run history, current process state, login state, and execution location."""
    resolved_id, entity_name = resolve_entity(watch, entity_id)
    platforms = [str(item) for item in (watch.get("platforms") or [])]
    runs, quality, notes = latest_platform_runs(store, resolved_id, platforms)

    bridge = {"ok": None, "message": "未检测"}
    login_rows: list[dict] = []
    if login_provider is not None:
        bridge_result, login_rows = login_provider(platforms)
        bridge = {"ok": bool(bridge_result[0]), "message": str(bridge_result[1])}
    login_by_platform = {item["platform"]: item for item in login_rows}
    for item in runs:
        platform = item["platform"]
        login = login_by_platform.get(platform)
        item["login_required"] = platform in _LOGIN_REQUIRED
        if login is not None:
            item["login"] = login
        elif item["login_required"]:
            item["login"] = {
                "platform": platform, "logged_in": False, "identity": "",
                "method": "unknown", "error": "未取得登录状态",
            }
        else:
            item["login"] = {
                "platform": platform, "logged_in": True, "identity": "",
                "method": "none", "error": "",
            }

    data = {
        "entity": {"id": resolved_id, "name": entity_name},
        "execution": execution_environment(),
        "run": {
            "running": bool(run_state.get("running")),
            "current": run_state.get("current") or "",
            "stop_requested": bool(run_state.get("stop")),
            "last": run_state.get("last"),
        },
        "bridge": bridge,
        "platforms": runs,
    }
    return data, quality, notes
