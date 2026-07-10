# -*- coding: utf-8 -*-
"""数据健康三态 + 静默失败熔断（立项规划的头号命门，不可砍）。

严格区分：
  ok       —— 采集正常，"没负面"是真的没负面
  suspect  —— 采集量相对基线骤降（疑似 cookie 过期/被风控/限流删帖）
  fail     —— 采集直接报错/返回 0
默认假设：空结果 = 抓取坏了，而不是平安无事。
"""

from __future__ import annotations

_ORDER = {"ok": 0, "suspect": 1, "fail": 2}
DROP_RATIO = 0.30  # 低于近期基线的 30% 判 suspect


def assess(store, *, platform: str, entity_id: str, n_fetched: int, status: str,
           entry: str = "", source_query: str = "") -> str:
    """单次 (实体,平台) 采集的健康态。

    关键前提：登录失效在我们所有平台都是**显式**的——opencli 适配器返回 AUTH_REQUIRED→异常，
    黑猫登录墙(无"退出")→raise，都走 status!='ok'→fail。所以"成功但 0 结果"= 该词真没内容
    （冷门/出海品牌常见），不该无脑判 fail 否则红条泛滥、静默失败机制反被稀释。
    """
    if status != "ok":
        return "fail"                                    # 采集异常/登录失效(显式)
    try:
        baseline = store.platform_baseline(
            platform, entity_id, entry=entry, source_query=source_query)
    except TypeError:  # 兼容测试桩/旧扩展实现
        baseline = store.platform_baseline(platform, entity_id)
    if n_fetched == 0:
        return "suspect" if baseline else "ok"           # 有历史却归零=可疑；无历史=真空
    if baseline and n_fetched < baseline * DROP_RATIO:
        return "suspect"
    return "ok"


def worst(a: str | None, b: str) -> str:
    """多实体/多次取最差态。"""
    if a is None:
        return b
    return a if _ORDER[a] >= _ORDER[b] else b


def banner(health_by_platform: dict[str, str]) -> str | None:
    """报告顶部红条：有任何非 ok 平台就返回警告文本，否则 None。"""
    bad = {p: s for p, s in health_by_platform.items() if s != "ok"}
    if not bad:
        return None
    fail = [p for p, s in bad.items() if s == "fail"]
    suspect = [p for p, s in bad.items() if s == "suspect"]
    parts = []
    if fail:
        parts.append(f"采集失败：{'、'.join(fail)}（这些平台**无数据**，不代表无负面）")
    if suspect:
        parts.append(f"采集量骤降存疑：{'、'.join(suspect)}（疑似 cookie 过期/被限流，数据可能不全）")
    return "⚠️ 数据健康告警 —— " + "；".join(parts) + "。本报告结论需人工核查后采用。"


if __name__ == "__main__":
    assert worst(None, "ok") == "ok"
    assert worst("ok", "fail") == "fail"
    assert worst("suspect", "ok") == "suspect"
    assert banner({"weibo": "ok", "zhihu": "ok"}) is None
    b = banner({"weibo": "ok", "zhihu": "fail", "heimao": "suspect"})
    assert b and "zhihu" in b and "heimao" in b

    # assess：登录失效(status!=ok)→fail；0结果无基线→ok(真空)；0结果有基线→suspect(归零可疑)
    class _S:
        def __init__(self, base): self._b = base
        def platform_baseline(self, p, e): return self._b
    assert assess(_S(None), platform="douyin", entity_id="e", n_fetched=0, status="ok") == "ok"
    assert assess(_S(40), platform="weibo", entity_id="e", n_fetched=0, status="ok") == "suspect"
    assert assess(_S(None), platform="weibo", entity_id="e", n_fetched=0, status="error") == "fail"
    assert assess(_S(40), platform="weibo", entity_id="e", n_fetched=3, status="ok") == "suspect"  # 骤降
    assert assess(_S(None), platform="weibo", entity_id="e", n_fetched=5, status="ok") == "ok"
    # 控制台可能是 GBK，去掉 emoji 再打印（红条本体在报告里仍是 UTF-8 完整的）
    print("OK health: 三态/红条/assess(登录失效vs真空vs骤降) 生效\n ", b.replace("⚠️", "[告警]"))
