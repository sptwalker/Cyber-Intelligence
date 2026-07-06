# -*- coding: utf-8 -*-
"""系统配置：飞书 + AI 模型 + 成本护栏。配置文件优先，环境变量兜底。

密钥落地在本地 yuqing_config.json（**已 gitignore，绝不进仓库**），与 env 同等明文风险，
仅适合内部单机。resolve() 让 llm/report/budget 统一读配置：文件有值用文件（UI 设置为准），
否则回退 os.getenv。设置页只脱敏显示密钥尾 4 位，绝不回显全量。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# 设置页暴露的字段： (key, 标签, 是否密钥需脱敏)
FIELDS = [
    ("FEISHU_WEBHOOK", "飞书机器人 Webhook", True),
    ("DEEPSEEK_API_KEY", "DeepSeek API Key", True),
    ("DEEPSEEK_BASE_URL", "DeepSeek Base URL（留空=官方默认）", False),
    ("DEEPSEEK_MODEL", "DeepSeek 模型（留空=deepseek-chat）", False),
    ("MINIMAX_API_KEY", "MiniMax API Key", True),
    ("MINIMAX_BASE_URL", "MiniMax Base URL（留空=默认）", False),
    ("MINIMAX_MODEL", "MiniMax 模型（留空=默认）", False),
    ("ANTHROPIC_API_KEY", "Claude API Key（可选）", True),
    ("YUQING_MAX_CALLS", "每日 LLM 调用上限", False),
    ("YUQING_MAX_TOKENS", "每日 Token 上限", False),
]
_SECRET = {k for k, _, s in FIELDS if s}


def _path() -> Path:
    return Path(os.getenv("YUQING_CONFIG", "yuqing_config.json"))


def load() -> dict:
    p = _path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _write(cfg: dict) -> None:
    _path().write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def save(form: dict) -> None:
    """从表单更新配置。密钥留空=保持原值（不必重输）；明文字段直接覆盖（空=清除回退默认）。"""
    cfg = load()
    for k, _, secret in FIELDS:
        if k not in form:
            continue
        v = (form.get(k) or "").strip()
        if secret:
            if v:                      # 脱敏字段留空即不改
                cfg[k] = v
        else:
            cfg[k] = v                 # 明文字段：所见即所存
    _write(cfg)


def resolve(key: str) -> str:
    """配置文件优先（UI 设置为准），回退环境变量。"""
    v = load().get(key)
    return v if v else os.getenv(key, "")


def masked() -> list:
    """给设置页渲染：secret 字段只显示尾 4 位。返回 [(key,label,secret,display,is_set)]。"""
    cfg = load()
    out = []
    for k, label, secret in FIELDS:
        val = cfg.get(k) or os.getenv(k, "")
        if secret:
            display = ("••••" + val[-4:]) if val else ""
        else:
            display = val
        out.append((k, label, secret, display, bool(val)))
    return out


if __name__ == "__main__":
    import tempfile
    os.environ["YUQING_CONFIG"] = tempfile.mktemp(suffix=".json")
    os.environ["DEEPSEEK_MODEL"] = "env-model"
    save({"FEISHU_WEBHOOK": "https://open.feishu.cn/xxx/secret123",
          "DEEPSEEK_API_KEY": "sk-abcd1234", "DEEPSEEK_BASE_URL": ""})
    assert resolve("FEISHU_WEBHOOK").endswith("secret123")          # 文件值
    assert resolve("DEEPSEEK_MODEL") == "env-model"                 # 回退 env
    save({"DEEPSEEK_API_KEY": ""})                                   # 密钥留空=保持
    assert resolve("DEEPSEEK_API_KEY") == "sk-abcd1234"
    m = {k: (disp, is_set) for k, _, _, disp, is_set in masked()}
    assert m["DEEPSEEK_API_KEY"][0] == "••••1234" and m["DEEPSEEK_API_KEY"][1]  # 脱敏尾4位
    assert "secret123" not in str(masked())                          # 绝不回显全量密钥
    os.remove(os.environ["YUQING_CONFIG"]); os.environ.pop("YUQING_CONFIG"); os.environ.pop("DEEPSEEK_MODEL")
    print("OK config: 文件优先/env兜底/密钥留空保持/脱敏尾4位/不回显全量 全通")
