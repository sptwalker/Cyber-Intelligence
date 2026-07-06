# -*- coding: utf-8 -*-
"""常驻调度 + deadman 心跳（工具→产品的分水岭：无人值守 7×24）。

- serve(): 常驻循环，按间隔跑 run.main()，每轮写心跳。ponytail: 用 stdlib time.sleep 循环，
  不引 APScheduler/cron 依赖；真部署可用 systemd/nssm 托管本进程，或 cron 直接调 run。
- deadman(): 供外部 cron 每小时调一次——若"距上次成功跑批"超过阈值，说明调度进程挂了/
  一直失败，推飞书叫人。这是"跑挂了自动告警"的核心（进程自己挂了没法自己报，必须外部探）。
- 登录态失效告警：连续多轮某平台 fail → 多半 cookie 过期/被风控，单独告警提示换号/重登。
"""

from __future__ import annotations

import datetime as _dt
import sys
import time
from typing import Optional

from .store import Store


def _now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _aware(dt: _dt.datetime) -> _dt.datetime:
    """naive datetime 附本地时区，避免与 aware 相减报 TypeError。"""
    return dt if dt.tzinfo else dt.astimezone()


def run_once(db: str = "yuqing.db", watch_path: str = "watch.yaml") -> str:
    """跑一轮并写心跳。绝不抛异常（含网络/心跳/告警出错），保证常驻循环不死。返回 ok/error。"""
    status, note, now = "error", "", _now()
    try:
        from .run import main as run_main
        code = run_main(watch_path, db)
        status, note = ("ok", "") if code == 0 else ("error", f"run.main 退出码 {code}")
    except Exception as e:                        # 跑批异常
        status, note = "error", str(e)[:200]
    try:                                          # 心跳+告警也兜底：推送超时等绝不能崩常驻
        s = Store(db)
        try:
            s.record_heartbeat(now, status, note)
            _check_login_failures(s)
        finally:
            s.close()
    except Exception as e:
        print(f"[run_once 心跳/告警兜底] {e}", file=sys.stderr)
    return status


LOGIN_FAIL_ROUNDS = 3        # 某平台连续 N *轮* fail → 疑似登录态失效
_HEALTH_ORDER = {"ok": 0, "suspect": 1, "fail": 2}


def _check_login_failures(store: Store) -> list[str]:
    """连续 N 轮（按 run_id 归轮，每轮取最差态）全 fail 的平台 → 推告警。返回受影响平台。

    注意：run_log 每(实体,平台)一行且同轮共享 ts，必须先按 run_id 归轮再数"连续"，
    否则同一轮多实体会被误当成多轮。
    """
    from .report import push_feishu
    rows = store.conn.execute(
        "SELECT run_id, platform, health FROM run_log ORDER BY ts DESC LIMIT 300").fetchall()
    per: dict[str, dict[str, str]] = {}          # platform -> {run_id: worst_health}（保序=最近优先）
    for r in rows:
        d = per.setdefault(r["platform"], {})
        cur = d.get(r["run_id"])
        if cur is None or _HEALTH_ORDER.get(r["health"], 0) > _HEALTH_ORDER.get(cur, 0):
            d[r["run_id"]] = r["health"]
    bad = []
    for platform, rounds in per.items():
        recent = list(rounds.values())[:LOGIN_FAIL_ROUNDS]   # 最近 N 轮
        if len(recent) >= LOGIN_FAIL_ROUNDS and all(h == "fail" for h in recent):
            bad.append(platform)
    if bad:
        push_feishu(f"⚠️ 登录态疑似失效：{'、'.join(bad)} 连续 {LOGIN_FAIL_ROUNDS} 轮采集失败，"
                    f"请检查 opencli 登录/换号重登。", title="舆情采集告警")
    return bad


def deadman(db: str = "yuqing.db", *, max_silence_min: int = 180,
            now: Optional[str] = None) -> Optional[str]:
    """外部 cron 调：距上次成功跑批超过阈值 → 判定调度死亡，推飞书。返回告警文本或 None。"""
    from .report import push_feishu
    s = Store(db)
    try:
        hb = s.get_heartbeat()
    finally:
        s.close()
    now_dt = _aware(_dt.datetime.fromisoformat(now)) if now else _dt.datetime.now().astimezone()
    if not hb or not hb.get("last_success"):
        msg = "☠️ deadman：从未成功跑批（调度可能从未启动或一直失败）"
        push_feishu(msg, title="舆情调度告警")
        return msg
    silence = (now_dt - _aware(_dt.datetime.fromisoformat(hb["last_success"]))).total_seconds() / 60
    if silence > max_silence_min:
        msg = (f"☠️ deadman：已 {silence:.0f} 分钟无成功跑批（阈值 {max_silence_min}），"
               f"调度进程可能已死。最后状态 {hb['last_status']}。请检查。")
        push_feishu(msg, title="舆情调度告警")
        return msg
    return None


def serve(db: str = "yuqing.db", watch_path: str = "watch.yaml", *, interval_min: int = 60) -> None:
    """常驻循环：每 interval_min 跑一轮，run_once 已兜底故循环永不因单轮异常退出。"""
    print(f"yuqing 常驻调度启动：每 {interval_min} 分钟一轮（Ctrl+C 停）")
    while True:
        print(f"[{_now()}] 跑批完成：{run_once(db, watch_path)}")
        time.sleep(interval_min * 60)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        # 离线自检：心跳前移 + deadman阈值(含naive now) + 登录态"按轮"连续失败告警（不触网）
        import os
        import tempfile
        from . import report as _r
        _sent = []
        _r.push_feishu = lambda *a, **k: (_sent.append(a[0]), True)[1]   # 拦截推送
        # 1) 心跳前移：失败不算存活，成功才前移
        s = Store(":memory:")
        s.record_heartbeat("2026-07-06T09:00:00+08:00", "error", "x")
        assert s.get_heartbeat()["last_success"] == ""
        s.record_heartbeat("2026-07-06T10:00:00+08:00", "ok")
        assert s.get_heartbeat()["last_success"] == "2026-07-06T10:00:00+08:00"
        # 2) deadman：真调函数（临时库）。30min<阈值→不告警；5h>阈值→告警；naive now 不崩
        tmp = tempfile.mktemp(suffix=".db")
        Store(tmp).record_heartbeat("2026-07-06T10:00:00+08:00", "ok")
        assert deadman(tmp, max_silence_min=180, now="2026-07-06T10:30:00+08:00") is None
        assert deadman(tmp, max_silence_min=180, now="2026-07-06T15:30:00+08:00")   # 告警
        assert deadman(tmp, max_silence_min=180, now="2026-07-06T15:30:00") is not None  # naive 不崩
        os.remove(tmp)
        # 3) 登录态"按轮"：同一轮(同run_id)多实体 fail 不算多轮；须 N 个不同 run_id 才告警
        s2 = Store(":memory:")
        s2.log_run("R1", "weibo", "e1", 0, "error", "fail", "", "2026-07-06T10:00:00+08:00")
        s2.log_run("R1", "weibo", "e2", 0, "error", "fail", "", "2026-07-06T10:00:00+08:00")
        s2.log_run("R1", "weibo", "e3", 0, "error", "fail", "", "2026-07-06T10:00:00+08:00")
        assert _check_login_failures(s2) == [], "同一轮多实体不应误判为多轮"
        for i, rid in enumerate(["R2", "R3"]):   # 再来两轮 → 共3个不同 run_id 全 fail
            s2.log_run(rid, "weibo", "e1", 0, "error", "fail", "", f"2026-07-06T1{i+1}:00:00+08:00")
        assert _check_login_failures(s2) == ["weibo"] and _sent, "连续3轮 fail 应告警"
        print("OK scheduler: 心跳前移✓ deadman阈值(含naive)✓ 登录态按轮连续失败告警✓")
    elif len(sys.argv) > 1 and sys.argv[1] == "deadman":
        print(deadman() or "调度存活，无需告警")
    elif len(sys.argv) > 1 and sys.argv[1] == "once":
        print("跑批状态:", run_once())
    else:
        serve(interval_min=int(sys.argv[1]) if len(sys.argv) > 1 else 60)
