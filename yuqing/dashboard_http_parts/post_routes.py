# -*- coding: utf-8 -*-
"""POST mutation routes retained for the legacy dashboard API."""

from __future__ import annotations

import datetime as _dt
import json
from urllib.parse import parse_qs


def _read_body(handler) -> str:
    length = int(handler.headers.get("Content-Length") or 0)
    return handler.rfile.read(length).decode("utf-8")


def _save_config(handler) -> None:
    app = handler._dashboard_app
    if not app._write_allowed(handler):
        handler.send_error(403)
        return
    from .. import config

    form = {key: values[0] for key, values in parse_qs(_read_body(handler)).items()}
    config.save(form)
    handler._send(app.render_config(saved=True))


def _run(handler) -> None:
    app = handler._dashboard_app
    if not app._mutation_allowed(handler):
        handler.send_error(403)
        return
    handler._send_legacy_json(app._start_background_run(handler._dashboard_db))


def _stop_run(handler) -> None:
    app = handler._dashboard_app
    if not app._mutation_allowed(handler):
        handler.send_error(403)
        return
    stop_result = app._request_run_stop()
    handler._send_legacy_json(
        {
            "success": stop_result["stop_requested"],
            "message": stop_result["message"],
        }
    )


def _open_login(handler) -> None:
    app = handler._dashboard_app
    if not app._write_allowed(handler):
        handler.send_error(403)
        return
    from .. import login

    raw = _read_body(handler)
    platform = (
        json.loads(raw).get("platform")
        if raw.startswith("{")
        else parse_qs(raw).get("platform", [""])[0]
    )
    try:
        login.open_login(platform)
        result = {
            "success": True,
            "message": f"已在浏览器打开 {platform} 登录页，请登录后点重新检测",
        }
    except Exception as exc:
        result = {"success": False, "message": str(exc)[:200]}
    handler._send_legacy_json(result)


def _save_watch(handler) -> None:
    app = handler._dashboard_app
    if not app._write_allowed(handler):
        handler.send_error(403)
        return
    # Import through the package facade so historical yuqing.watch_path patches keep working.
    from .. import watch_path

    raw = _read_body(handler)
    try:
        content = json.loads(raw).get("content", "") if raw.startswith("{") else ""
    except Exception:
        content = ""
    ok, message = app._validate_watch(content)
    if ok:
        try:
            import shutil

            path = watch_path()
            try:
                shutil.copyfile(path, path + ".bak")
            except FileNotFoundError:
                pass
            with open(path, "w", encoding="utf-8") as file:
                file.write(content)
            result = {
                "success": True,
                "message": message + "，已保存（下轮采集/刷新生效）",
            }
        except Exception as exc:
            result = {"success": False, "message": f"写入失败：{str(exc)[:200]}"}
    else:
        result = {"success": False, "message": message + "（未保存）"}
    handler._send_legacy_json(result)


def _mutate_keywords(handler) -> None:
    app = handler._dashboard_app
    if not app._mutation_allowed(handler):
        handler.send_error(403)
        return
    store = app.Store(handler._dashboard_db)
    try:
        from ..keywords import KeywordManager

        manager = KeywordManager(store)
        body_data = _read_body(handler)
        data = json.loads(body_data) if body_data.startswith("{") else parse_qs(body_data)
        if isinstance(data, dict) and not isinstance(list(data.values())[0], list):
            form = data
        else:
            form = {key: values[0] for key, values in data.items()}

        action = form.get("action", "")
        result = {"success": False, "message": ""}
        if action == "add":
            try:
                manager.add(
                    word=form["word"],
                    tag=form["tag"],
                    entity_id=form.get("entity_id") or None,
                    weight=float(form.get("weight", 1.0)),
                    note=form.get("note", ""),
                )
                result = {"success": True, "message": "添加成功"}
            except Exception as exc:
                result = {"success": False, "message": str(exc)}
        elif action == "delete":
            success = manager.remove(
                word=form["word"],
                tag=form["tag"],
                entity_id=form.get("entity_id") or None,
            )
            result = {"success": success, "message": "删除成功" if success else "未找到"}
        elif action == "approve":
            success = manager.approve_suggestion(int(form["id"]))
            result = {"success": success, "message": "已批准" if success else "失败"}
        elif action == "reject":
            success = manager.reject_suggestion(int(form["id"]))
            result = {"success": success, "message": "已拒绝" if success else "失败"}
        handler._send_legacy_json(result)
    finally:
        store.close()


def _save_annotation(handler) -> None:
    app = handler._dashboard_app
    if not app._mutation_allowed(handler):
        handler.send_error(403)
        return
    store = app.Store(handler._dashboard_db)
    try:
        from ..keywords import KeywordManager, STANCES, SUBJECTS

        data = json.loads(_read_body(handler))
        doc_id = data.get("doc_id")
        subject = data.get("subject") if data.get("subject") in SUBJECTS else None
        stance = data.get("stance") if data.get("stance") in STANCES else None
        if not doc_id or not subject or not stance:
            result = {"success": False, "message": "缺 doc_id/主体/立场，或枚举非法"}
        else:
            entity_id = data.get("entity_id")
            words = data.get("picked_words") or []
            now = _dt.datetime.now().isoformat(timespec="seconds")
            store.add_annotation(
                doc_id,
                subject=subject,
                stance=stance,
                importance=data.get("importance"),
                picked_words=words,
                note=data.get("note", ""),
                sample_source=data.get("sample_source", "manual"),
                entity_id=entity_id,
                ts=now,
            )
            manager = KeywordManager(store)
            for word_data in words:
                word = (word_data.get("word") or "").strip()
                role = word_data.get("role") or "related"
                if not word:
                    continue
                try:
                    manager.add_suggestion(
                        word,
                        role,
                        entity_id,
                        score=0.9,
                        reason="标注圈选",
                        source_docs=json.dumps([doc_id]),
                    )
                    if role == "product_name":
                        manager.add_suggestion(
                            word,
                            "seed_alias",
                            entity_id,
                            score=0.9,
                            reason="标注圈选·产品名",
                            source_docs=json.dumps([doc_id]),
                        )
                except Exception:
                    pass
            result = {"success": True, "message": "已保存"}
        handler._send_legacy_json(result)
    finally:
        store.close()


def _mutate_seed(handler) -> None:
    app = handler._dashboard_app
    if not app._mutation_allowed(handler):
        handler.send_error(403)
        return
    store = app.Store(handler._dashboard_db)
    try:
        from .. import analytics, load_watch
        from ..keywords import KeywordManager

        data = json.loads(_read_body(handler))
        action = data.get("action")
        manager = KeywordManager(store)
        if action == "mine":
            try:
                count = analytics.mine_and_queue(store, load_watch(), km=manager)
                result = {
                    "success": True,
                    "message": f"挖词完成：种子 {count['seed']} · 判别词 {count['feature']}",
                }
            except Exception as exc:
                result = {"success": False, "message": f"挖词失败：{str(exc)[:150]}"}
        elif action == "approve":
            suggestion = next(
                (
                    item
                    for item in manager.list_suggestions(status="pending", tag="seed_alias")
                    if item["id"] == int(data.get("id", 0))
                ),
                None,
            )
            if not suggestion:
                result = {"success": False, "message": "建议不存在"}
            else:
                ok, message = analytics.append_alias(
                    suggestion["entity_id"], suggestion["word"]
                )
                if ok:
                    manager.mark_suggestion(suggestion["id"], "approved")
                result = {"success": ok, "message": message}
        elif action == "reject":
            ok = manager.reject_suggestion(int(data.get("id", 0)))
            result = {"success": ok, "message": "已忽略" if ok else "失败"}
        else:
            result = {"success": False, "message": "未知操作"}
        handler._send_legacy_json(result)
    finally:
        store.close()


def _mutate_accounts(handler) -> None:
    app = handler._dashboard_app
    if not app._mutation_allowed(handler):
        handler.send_error(403)
        return
    store = app.Store(handler._dashboard_db)
    try:
        data = json.loads(_read_body(handler))
        action = data.get("action")
        if (
            action == "add"
            and (data.get("author") or "").strip()
            and data.get("subject_type") in ("官方", "准官方", "媒体")
        ):
            store.add_account(
                data["author"].strip(),
                data["subject_type"],
                platform=(data.get("platform") or "").strip(),
                note="",
                ts=_dt.datetime.now().isoformat(timespec="seconds"),
            )
            result = {"success": True, "message": "已添加"}
        elif action == "delete":
            result = {
                "success": store.delete_account(int(data.get("id", 0))),
                "message": "已删除",
            }
        else:
            result = {
                "success": False,
                "message": "参数不合法（账号必填，类型须官方/准官方/媒体）",
            }
        handler._send_legacy_json(result)
    finally:
        store.close()


def _transition_incident(handler) -> None:
    app = handler._dashboard_app
    if not app._mutation_allowed(handler):
        handler.send_error(403)
        return
    store = app.Store(handler._dashboard_db)
    try:
        from .. import alerts

        data = json.loads(_read_body(handler))
        user = app._require_auth(handler) or {}
        actor = user.get("name") or user.get("open_id") or "local"
        result = alerts.transition(
            store,
            data.get("incident_id", ""),
            data.get("action", ""),
            actor=actor,
            note=data.get("note", ""),
            now=_dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        )
        handler._send_legacy_json(
            result,
            200 if result.get("success") else 400,
            default_str=True,
        )
    finally:
        store.close()


_POST_ROUTES = {
    "/config": _save_config,
    "/api/run": _run,
    "/api/run/stop": _stop_run,
    "/api/login/open": _open_login,
    "/api/watch": _save_watch,
    "/api/keywords": _mutate_keywords,
    "/api/annotate": _save_annotation,
    "/api/seed": _mutate_seed,
    "/api/accounts": _mutate_accounts,
    "/api/incidents": _transition_incident,
}


def dispatch_legacy_post(handler, parsed_url) -> None:
    """Dispatch one legacy mutation endpoint or return the historical 404."""
    if parsed_url.path.startswith("/api/v1/"):
        handler._handle_api_v1_post(parsed_url)
        return
    action = _POST_ROUTES.get(parsed_url.path)
    if action is None:
        handler.send_error(404)
        return
    action(handler)
