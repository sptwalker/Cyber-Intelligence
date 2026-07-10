# -*- coding: utf-8 -*-
"""实时预警：分级(P0/P1) + 事件簇去重 + 冷却，抑制告警疲劳。

窄通道原则：只有高风险负面/危机才实时推；其余进周期报告。数据健康 fail/suspect
也发预警（静默失败告警）。冷却按事件簇（content_cluster），同簇 COOLDOWN_H 小时内不重复。
"""

from __future__ import annotations

import datetime as _dt
import json
from typing import Optional

from .report import push_feishu_alert_card
from .store import Store

P0_RISK = 100.0        # 大V危机负面量级
P1_RISK = 30.0         # 需当日跟进
COOLDOWN_H = 6


def _level(risk: float, crisis: bool) -> Optional[str]:
    if risk >= P0_RISK or (crisis and risk >= P1_RISK):
        return "P0"
    if risk >= P1_RISK:
        return "P1"
    return None          # 更低的只进报告，不实时推


def _cutoff(now: str, hours: int) -> str:
    return (_dt.datetime.fromisoformat(now) - _dt.timedelta(hours=hours)).isoformat()


def evaluate(store: Store, *, now: str, health_by_platform: Optional[dict] = None,
             self_entities: Optional[set] = None, cooldown_h: int = COOLDOWN_H) -> list[dict]:
    """算出本轮应发的预警（已去重+冷却），并登记以供后续冷却。返回预警列表。

    self_entities: 只对这些自有实体发风险预警；None=不过滤。竞品负面不应
    把客户当自家危机叫醒（竞品的锅不是你的锅）。
    """
    since = _cutoff(now, cooldown_h)
    out: list[dict] = []

    # 1) 舆情风险预警（仅自有实体）
    for r in store.joined_with_entities():
        if r["polarity"] != "neg":
            continue
        entity_id = r["matched_entity_id"]
        if self_entities is not None and entity_id not in self_entities:
            continue                                     # 竞品负面不告警
        signals = json.loads(r["signals"] or "{}")
        lvl = _level(r["risk"] or 0.0, bool(signals.get("crisis")))
        if not lvl:
            continue
        raw_cluster = r["content_cluster"] or r["doc_id"]
        ck = f"risk:{entity_id}:{raw_cluster}"        # 实体隔离，避免多品牌同文案互相抑制
        if store.recent_alert(ck, since):
            continue
        store.record_alert(ck, lvl, r["doc_id"], r["summary"] or "", now)
        incident = store.create_incident(
            entity_id=entity_id, cluster_key=raw_cluster, level=lvl,
            doc_id=r["doc_id"], summary=r["summary"] or "", ts=now)
        out.append({"level": lvl, "kind": "risk", "platform": r["platform"],
                    "risk": r["risk"], "summary": r["summary"] or "", "url": r["url"] or "",
                    "doc_id": r["doc_id"], "entity_id": entity_id,
                    "incident_id": incident["incident_id"], "status": incident["status"]})

    # 2) 数据健康预警（静默失败）——fail/suspect 各自成簇冷却
    for platform, state in (health_by_platform or {}).items():
        if state == "ok":
            continue
        ck = f"health:{platform}:{state}"
        if store.recent_alert(ck, since):
            continue
        lvl = "P0" if state == "fail" else "P1"
        msg = f"{platform} 采集{'失败' if state == 'fail' else '量骤降存疑'}——数据可能不全，勿当无负面"
        store.record_alert(ck, lvl, "", msg, now)
        out.append({"level": lvl, "kind": "health", "platform": platform, "summary": msg})

    # 风险高的排前面
    out.sort(key=lambda a: (a["level"] != "P0", -(a.get("risk") or 0)))
    return out


def format_card(alerts: list[dict]) -> str:
    """预警合并成一张飞书卡片文本。"""
    lines = [f"🚨 舆情预警 {len(alerts)} 条"]
    for a in alerts:
        if a["kind"] == "health":
            lines.append(f"[{a['level']}] 数据健康：{a['summary']}")
        else:
            lines.append(f"[{a['level']}] {a['platform']} 风险{a['risk']}：{a['summary'][:40]} {a.get('url','')}")
    return "\n".join(lines)


def dispatch(store: Store, *, now: str, health_by_platform: Optional[dict] = None,
             self_entities: Optional[set] = None, webhook: Optional[str] = None) -> list[dict]:
    """算 + 推到分析师确认通道。P0/P1 均先待确认，不直接进入高层通道。"""
    alerts = evaluate(store, now=now, health_by_platform=health_by_platform,
                      self_entities=self_entities)
    store.commit()
    if alerts:
        if webhook is None:
            from . import config
            webhook = config.resolve("FEISHU_ALERT_WEBHOOK") or config.resolve("FEISHU_WEBHOOK")
        push_feishu_alert_card(alerts, webhook=webhook)
    return alerts


def transition(store: Store, incident_id: str, action: str, *, actor: str,
               now: str, note: str = "", executive_webhook: Optional[str] = None) -> dict:
    """人工处置事件；P0 confirmed 后才升级到高层通道。"""
    target = {"confirm": "confirmed", "suppress": "suppressed",
              "escalate": "escalated", "resolve": "resolved"}.get(action)
    incident = store.get_incident(incident_id)
    if not target or not incident:
        return {"success": False, "message": "事件不存在或操作非法"}
    from . import config
    executive_webhook = executive_webhook or config.resolve("FEISHU_EXEC_WEBHOOK")

    def _push_executive(item: dict) -> bool:
        if not executive_webhook:
            return False
        try:
            return push_feishu_alert_card([{
                "level": item["level"], "kind": "risk", "platform": "已人工确认",
                "risk": "—", "summary": item["summary"], "url": "",
                "incident_id": incident_id,
            }], webhook=executive_webhook)
        except Exception:
            return False

    # 显式 escalate 必须先真正触达高层，失败则不伪造状态。
    if target == "escalated":
        if incident["status"] != "confirmed":
            return {"success": False, "message": f"不允许从 {incident['status']} 直接升级高层"}
        if not _push_executive(incident):
            return {"success": False, "message": "高层 Webhook 未配置或推送失败，事件仍保持 confirmed"}
    if not store.transition_incident(incident_id, target, actor=actor, note=note, ts=now):
        return {"success": False, "message": f"不允许从 {incident['status']} 执行 {action}"}
    updated = store.get_incident(incident_id)
    pushed = target == "escalated"
    if target == "confirmed" and updated["level"] == "P0":
        pushed = _push_executive(updated)
        if pushed:
            store.transition_incident(incident_id, "escalated", actor=actor,
                                      note=note or "P0 已推送高层", ts=now)
            updated = store.get_incident(incident_id)
    return {"success": True, "incident": updated, "executive_pushed": pushed}


if __name__ == "__main__":
    assert _level(150, False) == "P0"
    assert _level(50, True) == "P0" and _level(50, False) == "P1"
    assert _level(10, True) is None
    print("OK alerts: 分级阈值正确")
