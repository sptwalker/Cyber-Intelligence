# -*- coding: utf-8 -*-
"""LLM 接入层：deepseek / MiniMax（OpenAI 兼容 chat/completions，JSON 输出）。

stdlib urllib，不加依赖。base_url/model 走 env 可覆盖（MiniMax 端点/模型名是校准点，
首次带 key 用 `python -m yuqing.llm ping minimax` 验证连通再跑批）。
"""

from __future__ import annotations

import json
import urllib.request

from . import config

# 每个 provider 的 env 键与默认值（OpenAI 兼容 /chat/completions）
_PROVIDERS = {
    "deepseek": {"key": "DEEPSEEK_API_KEY", "base": "DEEPSEEK_BASE_URL",
                 "base_def": "https://api.deepseek.com", "model": "DEEPSEEK_MODEL",
                 "model_def": "deepseek-chat"},
    "minimax": {"key": "MINIMAX_API_KEY", "base": "MINIMAX_BASE_URL",
                "base_def": "https://api.minimaxi.com/v1", "model": "MINIMAX_MODEL",
                "model_def": "MiniMax-Text-01"},
}


def available(provider: str) -> bool:
    p = _PROVIDERS.get(provider)
    return bool(p and config.resolve(p["key"]))


def _build_payload(model: str, system: str, user: str) -> dict:
    return {"model": model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "response_format": {"type": "json_object"},
            "temperature": 0.2}


def _parse_content(resp: dict) -> dict:
    """从 OpenAI 兼容响应取出 message.content 并解析为 JSON。"""
    content = resp["choices"][0]["message"]["content"]
    return json.loads(content)


def chat_json(provider: str, system: str, user: str, *, timeout: int = 90) -> dict:
    """调 provider 返回解析后的 JSON dict。需对应 API key。"""
    cfg = _PROVIDERS[provider]
    key = config.resolve(cfg["key"])
    if not key:
        raise RuntimeError(f"{cfg['key']} 未配置")
    url = (config.resolve(cfg["base"]) or cfg["base_def"]).rstrip("/") + "/chat/completions"
    model = config.resolve(cfg["model"]) or cfg["model_def"]
    body = json.dumps(_build_payload(model, system, user)).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Authorization": f"Bearer {key}",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return _parse_content(json.loads(r.read().decode("utf-8")))


def probe(provider: str) -> tuple[bool, str]:
    """连通测试（供设置页"测试"按钮）。返回 (是否通, 说明)。"""
    if not available(provider):
        return False, "未配置 API Key"
    try:
        r = chat_json(provider, "只返回JSON", '返回 {"ok":true}', timeout=20)
        return True, f"连通 ✓（返回 {json.dumps(r, ensure_ascii=False)[:80]}）"
    except Exception as e:
        return False, f"失败：{str(e)[:200]}"


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "ping":     # 带 key 时验证连通：python -m yuqing.llm ping deepseek
        prov = sys.argv[2] if len(sys.argv) > 2 else "deepseek"
        print(f"{prov} available={available(prov)}")
        if available(prov):
            print(chat_json(prov, "你返回JSON", '返回 {"ok":true}'))
    else:
        # 离线自检：payload 构造 + 响应解析（纯函数，不触网）
        pl = _build_payload("deepseek-chat", "sys", "usr")
        assert pl["response_format"]["type"] == "json_object" and pl["messages"][1]["content"] == "usr"
        got = _parse_content({"choices": [{"message": {"content": '{"items":[{"polarity":"neg"}]}'}}]})
        assert got["items"][0]["polarity"] == "neg"
        assert available("deepseek") in (True, False)
        print("OK llm: payload 构造 + 响应解析 正确（provider 连通需带 key ping）")
