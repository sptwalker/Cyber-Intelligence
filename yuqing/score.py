# -*- coding: utf-8 -*-
"""线性加权风险分——唯一一套权重，可解释、冷启动不需标注数据。

风险分 = signal_weight × 负面强度 × log(1+互动) × log(1+粉丝)
影响力做对数压缩并封顶，防单个大 V 主导大盘。权重全部 config 旋钮。
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class Weights:
    # 平台信号权重（signal_weight）：黑猫投诉负面价值最高，微博传播最快。唯一权重来源。
    platform: dict = None
    crisis_boost: float = 1.5     # 命中危机词的乘子
    complaint_boost: float = 1.3  # 命中投诉的乘子
    fans_cap: float = 7.0         # log10(粉丝) 封顶，压住刷号大V

    def __post_init__(self):
        if self.platform is None:
            self.platform = {"heimao": 1.4, "weibo": 1.2, "zhihu": 1.1, "douyin": 1.0,
                             "xiaohongshu": 1.0, "bilibili": 1.0}


def _influence(likes: int, comments: int, reposts: int, followers: int, w: Weights) -> float:
    # (1 + …) 给"存在本身"一个下限：黑猫投诉常零互动但价值高，不能被 log(0) 归零。
    interact = 1 + math.log1p(likes + 2 * comments + 3 * reposts)   # 转发权重最高
    fans = 1 + min(math.log10(1 + max(followers, 0)), w.fans_cap)   # log10(粉丝) 封顶，压住刷号大V
    return interact * fans


def risk_score(row: dict, w: Weights) -> float:
    """row: clean⋈features 的一行（dict-like）。仅负面帖有正风险分。"""
    if row.get("polarity") != "neg":
        return 0.0
    signals = row.get("signals") or {}
    neg = float(row.get("intensity") or 0.0)
    base = w.platform.get(row.get("platform"), 1.0)
    if signals.get("crisis"):
        base *= w.crisis_boost
    if row.get("is_complaint"):
        base *= w.complaint_boost
    infl = _influence(row.get("likes", 0), row.get("comments", 0),
                      row.get("reposts", 0), row.get("author_followers", 0), w)
    return round(base * neg * infl, 3)


def influence_degraded(row: dict) -> bool:
    """无任何互动/粉丝数据 → 影响力项塌缩为存在下限，风险分是"降级"的（如微博搜索无点赞/转发）。

    这类分数只反映"命中危机词/投诉"，不含真实传播影响力，报告须显式标注、不可当全量真值。
    """
    return not any((row.get("likes") or 0, row.get("comments") or 0,
                    row.get("reposts") or 0, row.get("author_followers") or 0))


if __name__ == "__main__":
    w = Weights()
    big = {"polarity": "neg", "intensity": 0.9, "platform": "weibo", "is_complaint": True,
           "signals": {"crisis": True}, "likes": 5000, "comments": 800, "reposts": 2000,
           "author_followers": 3_000_000}
    small = {"polarity": "neg", "intensity": 0.5, "platform": "zhihu", "is_complaint": False,
             "signals": {}, "likes": 3, "comments": 1, "reposts": 0, "author_followers": 50}
    pos = {"polarity": "pos", "intensity": 0.9, "platform": "weibo", "likes": 9999,
           "signals": {}, "comments": 0, "reposts": 0, "author_followers": 1_000_000}
    rb, rs, rp = risk_score(big, w), risk_score(small, w), risk_score(pos, w)
    assert rb > rs > 0, (rb, rs)          # 大V危机负面 > 素人负面
    assert rp == 0.0                       # 正面不计风险，哪怕互动爆表
    assert risk_score({**big, "author_followers": 10**9}, w) < rb * 3  # 封顶生效
    # 影响力降级检测：无任何互动/粉丝 → True（微博搜索场景）
    assert influence_degraded({"platform": "weibo", "likes": 0, "comments": 0, "reposts": 0,
                               "author_followers": 0})
    assert not influence_degraded(big)
    print(f"OK score: 大V={rb} 素人={rs} 正面={rp}")
