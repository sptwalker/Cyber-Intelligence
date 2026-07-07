# -*- coding: utf-8 -*-
"""相关性/串味过滤（v1 数据质量地基，最高杠杆）。

搜索引擎极易串味：搜 "Youdoo Box" 串出 "Doo Prime" 外汇平台、创维品类新闻。
入库前用 别名(include) + 否定词(must_not) 判定一条帖子是否真的关于监控对象：
  - 命中任一 must_not 否定词 → 不相关（drop_reason=must_not）
  - 一个别名都不含 → 不相关（drop_reason=no_alias）
  - 否则 → 相关
must_not 语义 = "同名歧义/别的实体"的硬排除（如 Youdoo↔Doo Prime 外汇），**切勿放竞品名**：
竞品比较帖("Youdoo 比 X 好")是有价值的，且纯竞品新闻已被"不含别名"过滤掉。
纯函数，零依赖，可独立自检。词表来自 watch.yaml 的 entity，可持续人工收紧。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class Verdict:
    relevant: bool
    reason: str = ""          # "" | "must_not:<词>" | "no_alias"


def judge(text: str, aliases: Optional[list[str]] = None, must_not: Optional[list[str]] = None,
          *, require_alias: bool = True, sem_sim: Optional[float] = None,
          sem_threshold: Optional[float] = None) -> Verdict:
    """判定一条正文是否与监控对象相关。大小写不敏感（覆盖 Youdoo/youdoo/YOUDOO）。

    require_alias=False 时只做否定词过滤（适合 user-posts 等已定向来源，不强求正文含品牌名）。
    aliases/must_not 传 None 安全（当空列表处理）。

    语义通道（V2-B，默认关）：传入 sem_sim(该帖与监控对象的语义相似度)+sem_threshold 时，
    别名子串没命中但语义相似 ≥ 阈值 → 判相关(reason=semantic)，召回不含品牌字面但在议论该产品的帖。
    must_not 仍是硬排除、优先级最高（语义也救不回同名歧义）。
    """
    t = (text or "").lower()
    for neg in (must_not or []):
        neg = (neg or "").strip().lower()
        if neg and neg in t:
            return Verdict(False, f"must_not:{neg}")
    if require_alias:
        hit = any((a or "").strip().lower() in t for a in (aliases or []) if (a or "").strip())
        if not hit:
            if sem_threshold is not None and sem_sim is not None and sem_sim >= sem_threshold:
                return Verdict(True, f"semantic:{round(sem_sim, 3)}")   # 语义救回
            return Verdict(False, "no_alias")
    return Verdict(True)


if __name__ == "__main__":
    aliases = ["Youdoo Box", "Youdoo", "有度盒子"]
    must_not = ["Doo Prime"]      # 只放同名歧义，不放竞品名
    # 串味：Doo Prime 外汇（不含 Youdoo 别名）→ 无别名过滤
    assert not judge("警惕Doo Prime！账户被封", aliases, must_not).relevant
    # 纯竞品新闻（不含别名）→ no_alias 过滤（无需把竞品名放 must_not）
    assert judge("创维发布AI游戏主机", aliases, must_not).reason == "no_alias"
    # 竞品比较帖（含别名）→ 保留！竞品名不在 must_not，comparison 是有价值的
    assert judge("Youdoo Box 比创维盒子好用", aliases, must_not).relevant
    # 无关：一个别名都不含
    v = judge("今天天气不错", aliases, must_not)
    assert not v.relevant and v.reason == "no_alias"
    # 相关：含别名、不含否定词（大小写混合）
    assert judge("我的 YOUDOO box 发热严重", aliases, must_not).relevant
    assert judge("有度盒子好用", aliases, must_not).relevant
    # user-posts 定向来源：不强求含别名，只挡否定词
    assert judge("随便发的动态", aliases, must_not, require_alias=False).relevant
    # 健壮性：aliases=None/空 不崩
    assert judge("任意文本", None).relevant is False and judge("x", []).reason == "no_alias"
    assert judge("任意文本", None, require_alias=False).relevant   # 无别名要求时 None 也安全
    # V2-B 语义通道（默认关）：不含别名但语义相似≥阈值→救回；低于阈值→仍 no_alias；must_not 仍硬排除
    assert judge("这盒子巨卡发热", aliases, must_not, sem_sim=0.8, sem_threshold=0.7).relevant  # 语义救回
    assert judge("这盒子巨卡发热", aliases, must_not, sem_sim=0.8, sem_threshold=0.7).reason.startswith("semantic")
    assert judge("这盒子巨卡", aliases, must_not, sem_sim=0.5, sem_threshold=0.7).reason == "no_alias"  # 低于阈值不救
    assert not judge("Doo Prime账户", aliases, must_not, sem_sim=0.99, sem_threshold=0.7).relevant  # must_not硬排除,语义救不回
    assert judge("这盒子巨卡", aliases, must_not).reason == "no_alias"  # 不传语义参数=默认关,行为不变
    print("OK relevance: 串味过滤(否定词/别名/大小写/竞品比较/定向/None) + 语义通道(救回/阈值/must_not优先/默认关) 全通")
