# -*- coding: utf-8 -*-
"""Safe monitoring configuration, keyword, and seed-suggestion APIs."""

from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

import yaml

from .. import analytics, watch_path
from ..keywords import KeywordManager, TAGS
from ..watch_config import validate_watch
from .entities import configured_entities, resolve_entity
from .responses import APIError


SUPPORTED_PLATFORMS = (
    "weibo", "zhihu", "xiaohongshu", "douyin", "bilibili",
    "tieba", "hupu", "smzdm", "weixin", "heimao",
)
PLATFORM_LABELS = {
    "weibo": "微博", "zhihu": "知乎", "xiaohongshu": "小红书", "douyin": "抖音",
    "bilibili": "B站", "tieba": "贴吧", "hupu": "虎扑", "smzdm": "值得买",
    "weixin": "公众号", "heimao": "黑猫投诉",
}
EDITABLE_ENTITY_FIELDS = ("aliases", "must_not", "crisis_boost", "track_users")


def _string_list(value: Any, *, field: str, required: bool = False) -> list[str]:
    if not isinstance(value, list):
        raise APIError("INVALID_WATCH", f"{field} 必须是列表")
    result = []
    for item in value:
        text = str(item or "").strip()
        if not text:
            continue
        if len(text) > 100:
            raise APIError("INVALID_WATCH", f"{field} 的词条不能超过 100 个字符")
        if text not in result:
            result.append(text)
    if required and not result:
        raise APIError("INVALID_WATCH", f"{field} 至少需要一项")
    if len(result) > 100:
        raise APIError("INVALID_WATCH", f"{field} 最多支持 100 项")
    return result


def build_watch_config(watch: dict, *, entity_id: str | None = None) -> dict[str, Any]:
    resolved_id, entity_name = resolve_entity(watch, entity_id)
    entities = []
    for item in watch.get("entities") or []:
        entities.append({
            "id": str(item.get("id") or ""),
            "name": str((item.get("aliases") or [item.get("id") or ""])[0]),
            "type": str(item.get("type", "self")),
            **{field: [str(value) for value in (item.get(field) or [])] for field in EDITABLE_ENTITY_FIELDS},
        })
    enabled = [str(item) for item in (watch.get("platforms") or [])]
    return {
        "entity": {"id": resolved_id, "name": entity_name},
        "entities": entities,
        "platforms": [
            {"id": platform, "name": PLATFORM_LABELS[platform], "enabled": platform in enabled}
            for platform in SUPPORTED_PLATFORMS
        ],
    }


def update_watch_config(
    current: dict, payload: dict[str, Any], *, path: str | Path | None = None,
) -> dict[str, Any]:
    """Validate, back up, and atomically replace the effective watch YAML."""
    platforms = _string_list(payload.get("platforms"), field="platforms", required=True)
    unsupported = [item for item in platforms if item not in SUPPORTED_PLATFORMS]
    if unsupported:
        raise APIError("INVALID_WATCH", "不支持的平台：" + "、".join(unsupported))
    submitted_entities = payload.get("entities")
    if not isinstance(submitted_entities, list) or not submitted_entities:
        raise APIError("INVALID_WATCH", "entities 至少需要一个监控对象")

    original_by_id = {
        str(item.get("id")): item for item in (current.get("entities") or []) if item.get("id")
    }
    submitted_by_id = {
        str(item.get("id")): item for item in submitted_entities
        if isinstance(item, dict) and item.get("id")
    }
    if set(original_by_id) != set(submitted_by_id):
        raise APIError("INVALID_WATCH", "一期仅允许编辑现有监控对象")

    updated = copy.deepcopy(current)
    updated["platforms"] = platforms
    for entity in updated.get("entities") or []:
        entity_id = str(entity["id"])
        submitted = submitted_by_id[entity_id]
        entity["aliases"] = _string_list(
            submitted.get("aliases"), field=f"{entity_id}.aliases", required=True,
        )
        for field in EDITABLE_ENTITY_FIELDS[1:]:
            entity[field] = _string_list(
                submitted.get(field, []), field=f"{entity_id}.{field}",
            )

    text = yaml.safe_dump(updated, allow_unicode=True, sort_keys=False, width=120)
    valid, message = validate_watch(text)
    if not valid:
        raise APIError("INVALID_WATCH", message)

    target = Path(path or watch_path())
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.copyfile(target, Path(str(target) + ".bak"))
    handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=target.parent, prefix=target.name + ".", delete=False,
    )
    try:
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, target)
    except Exception:
        try:
            os.unlink(handle.name)
        except FileNotFoundError:
            pass
        raise
    return updated


def build_keywords(store, watch: dict, *, entity_id: str | None = None) -> dict[str, Any]:
    resolved_id, entity_name = resolve_entity(watch, entity_id)
    manager = KeywordManager(store)
    return {
        "entity": {"id": resolved_id, "name": entity_name},
        "items": manager.list(entity_id=resolved_id),
        "suggestions": manager.list_suggestions(
            status="pending", entity_id=resolved_id, exclude_tag="seed_alias",
        ),
        "tags": [{"value": value, "label": label} for value, label in TAGS.items()],
    }


def mutate_keyword(
    store, watch: dict, payload: dict[str, Any], *, entity_id: str | None = None,
) -> dict[str, Any]:
    resolved_id, _ = resolve_entity(watch, entity_id)
    manager = KeywordManager(store)
    action = str(payload.get("action") or "").strip()
    if action == "add":
        word = str(payload.get("word") or "").strip()
        tag = str(payload.get("tag") or "").strip()
        try:
            weight = float(payload.get("weight", 1.0))
        except (TypeError, ValueError) as exc:
            raise APIError("INVALID_KEYWORD", "关键词权重必须是数字") from exc
        if not 0 <= weight <= 1:
            raise APIError("INVALID_KEYWORD", "关键词权重必须在 0～1 之间")
        try:
            keyword_id = manager.add(
                word, tag, resolved_id, weight=weight,
                note=str(payload.get("note") or "")[:500],
            )
        except ValueError as exc:
            raise APIError("INVALID_KEYWORD", str(exc)) from exc
        return {"action": action, "keyword_id": keyword_id}
    if action == "delete":
        removed = manager.remove(
            str(payload.get("word") or "").strip(),
            str(payload.get("tag") or "").strip(), resolved_id,
        )
        if not removed:
            raise APIError("NOT_FOUND", "关键词不存在", 404)
        return {"action": action, "removed": True}
    if action in {"approve", "reject"}:
        try:
            suggestion_id = int(payload.get("id"))
        except (TypeError, ValueError) as exc:
            raise APIError("INVALID_KEYWORD", "建议 ID 非法") from exc
        if action == "approve":
            changed = manager.approve_suggestion(suggestion_id)
        else:
            changed = manager.reject_suggestion(suggestion_id, str(payload.get("reason") or "")[:500])
        if not changed:
            raise APIError("NOT_FOUND", "关键词建议不存在", 404)
        return {"action": action, "suggestion_id": suggestion_id}
    raise APIError("INVALID_ACTION", "关键词操作不受支持")


def build_seeds(store, watch: dict, *, entity_id: str | None = None) -> dict[str, Any]:
    resolved_id, entity_name = resolve_entity(watch, entity_id)
    manager = KeywordManager(store)
    return {
        "entity": {"id": resolved_id, "name": entity_name},
        "items": manager.list_suggestions(
            status="pending", entity_id=resolved_id, tag="seed_alias",
        ),
    }


def mutate_seed(
    store, watch: dict, payload: dict[str, Any], *, entity_id: str | None = None,
    path: str | Path | None = None,
) -> tuple[dict[str, Any], dict]:
    resolved_id, _ = resolve_entity(watch, entity_id)
    manager = KeywordManager(store)
    action = str(payload.get("action") or "").strip()
    if action == "mine":
        count = analytics.mine_and_queue(store, watch, km=manager)
        return {"action": action, "queued": count}, watch
    try:
        suggestion_id = int(payload.get("id"))
    except (TypeError, ValueError) as exc:
        raise APIError("INVALID_SEED", "建议 ID 非法") from exc
    suggestion = next((
        item for item in manager.list_suggestions(
            status="pending", entity_id=resolved_id, tag="seed_alias",
        ) if item["id"] == suggestion_id
    ), None)
    if suggestion is None:
        raise APIError("NOT_FOUND", "种子建议不存在", 404)
    if action == "reject":
        manager.reject_suggestion(suggestion_id, str(payload.get("reason") or "")[:500])
        return {"action": action, "suggestion_id": suggestion_id}, watch
    if action != "approve":
        raise APIError("INVALID_ACTION", "种子操作不受支持")

    updated_entities = []
    for entity in watch.get("entities") or []:
        editable = {
            "id": entity["id"],
            **{field: list(entity.get(field) or []) for field in EDITABLE_ENTITY_FIELDS},
        }
        if str(entity["id"]) == resolved_id and suggestion["word"] not in editable["aliases"]:
            editable["aliases"].append(suggestion["word"])
        updated_entities.append(editable)
    updated = update_watch_config(
        watch,
        {"platforms": list(watch.get("platforms") or []), "entities": updated_entities},
        path=path,
    )
    manager.mark_suggestion(suggestion_id, "approved")
    return {"action": action, "suggestion_id": suggestion_id, "word": suggestion["word"]}, updated
