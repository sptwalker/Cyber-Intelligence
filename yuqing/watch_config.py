# -*- coding: utf-8 -*-
"""Loading, path resolution, and validation for monitoring configuration."""

from __future__ import annotations

import os


def watch_path(path: str = "watch.yaml") -> str:
    """Resolve the effective ``watch.yaml`` path from one shared boundary."""
    configured = os.getenv("YUQING_WATCH_PATH", "").strip()
    if configured:
        return configured
    if os.path.exists(path):
        return path
    packaged = os.path.join(os.path.dirname(__file__), "watch.yaml")
    return packaged if os.path.exists(packaged) else path


def load_watch(path: str = "watch.yaml") -> dict:
    """Load the single source of truth, retaining the packaged-file fallback."""
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("需要 PyYAML：pip install pyyaml") from exc
    resolved = watch_path(path)
    if not os.path.exists(resolved) and os.getenv("YUQING_WATCH_PATH"):
        resolved = os.path.join(os.path.dirname(__file__), "watch.yaml")
    with open(resolved, encoding="utf-8") as stream:
        return yaml.safe_load(stream)


def validate_watch(text: str) -> tuple[bool, str]:
    """Validate ``watch.yaml`` text and return ``(is_valid, message)``.

    This contract is shared by the dashboard editor, the versioned watch API,
    and analytics seed promotion.  Keeping it independent of the HTTP/UI layer
    prevents domain code from importing the dashboard module.
    """
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("需要 PyYAML：pip install pyyaml") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return False, f"YAML 语法错误：{str(exc)[:200]}"
    if not isinstance(data, dict):
        return False, "顶层必须是映射（含 platforms 和 entities）"
    if not isinstance(data.get("platforms"), list) or not data["platforms"]:
        return False, "缺少 platforms 列表（如 [weibo, zhihu, ...]）"
    entities = data.get("entities")
    if not isinstance(entities, list) or not entities:
        return False, "缺少 entities 列表（至少一个监控对象）"
    for index, entity in enumerate(entities):
        if not isinstance(entity, dict) or not entity.get("id"):
            return False, f"第 {index + 1} 个 entity 缺少 id"
        if entity.get("aliases") is not None and not isinstance(entity["aliases"], list):
            return False, f"entity {entity.get('id')} 的 aliases 必须是列表"
    return True, f"✓ 合法：{len(data['platforms'])} 个平台 / {len(entities)} 个实体"
