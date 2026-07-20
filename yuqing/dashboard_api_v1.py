# -*- coding: utf-8 -*-
"""Versioned dashboard API route handlers."""

from __future__ import annotations

import datetime as _dt
import json
import re
from urllib.parse import parse_qs, unquote

def handle_get(self, u, db: str, app) -> None:
    from .api.overview import RANGES, build_overview, configured_entities, resolve_entity
    from .api.responses import APIError, enum_value, query_value, success_payload

    if u.path == "/api/v1/readiness":
        try:
            from . import load_watch
            from . import collector_client
            watch = load_watch()
            store = app.Store(db)
            try:
                data, quality, notes = build_overview(store, watch, range_name="7d")
                schema_version = store.schema_version()
            finally:
                store.close()
            if schema_version != 2 or not (app._WORKBENCH_DIR / "index.html").is_file():
                raise RuntimeError("delivery baseline unavailable")
            collector = (
                collector_client.selfcheck(timeout=5)
                if collector_client.enabled() else None
            )
        except Exception:
            self._send_api_error("NOT_READY", "服务尚未就绪", 503)
            return
        readiness = {"ready": True, "schema_version": schema_version}
        if collector is not None:
            readiness["collector"] = collector
        self._send_json(success_payload(
            readiness,
            entity_id=data["entity"]["id"], data_quality=quality,
            quality_notes=notes,
        ))
        return
    if self._api_principal() is None:
        self._send_api_error("UNAUTHORIZED", "请先登录", 401)
        return
    incident_match = re.fullmatch(r"/api/v1/incidents/([0-9A-Za-z_-]+)", u.path)
    report_match = re.fullmatch(r"/api/v1/reports/([^/]+)", u.path)
    document_match = re.fullmatch(r"/api/v1/docs/([0-9a-f]{6,16})", u.path)
    if u.path not in {
        "/api/v1/overview", "/api/v1/collection/status",
        "/api/v1/collection/login-status", "/api/v1/analysis", "/api/v1/incidents",
        "/api/v1/backlog", "/api/v1/backlog.csv", "/api/v1/reviews", "/api/v1/reports",
        "/api/v1/context", "/api/v1/watch", "/api/v1/keywords", "/api/v1/seeds",
    } and incident_match is None and report_match is None and document_match is None:
        self._send_api_error("NOT_FOUND", "接口不存在", 404)
        return

    query = parse_qs(u.query, keep_blank_values=True)
    try:
        requested_entity = query_value(query, "entity_id")
        from . import load_watch
        watch = load_watch()
        if u.path == "/api/v1/context":
            resolved_id, entity_name = resolve_entity(watch, requested_entity)
            data = {
                "entity": {"id": resolved_id, "name": entity_name},
                "entities": configured_entities(watch),
                "ranges": [
                    {"value": "7d", "label": "近 7 天"},
                    {"value": "30d", "label": "近 30 天"},
                    {"value": "90d", "label": "近 90 天"},
                ],
                "user": self._api_principal(),
            }
            quality, quality_notes = "ok", []
        elif u.path in {"/api/v1/watch", "/api/v1/keywords", "/api/v1/seeds"}:
            from .api.watch import build_keywords, build_seeds, build_watch_config
            if u.path == "/api/v1/watch":
                data = build_watch_config(watch, entity_id=requested_entity)
            else:
                store = app.Store(db)
                try:
                    data = (
                        build_keywords(store, watch, entity_id=requested_entity)
                        if u.path == "/api/v1/keywords"
                        else build_seeds(store, watch, entity_id=requested_entity)
                    )
                finally:
                    store.close()
            quality, quality_notes = "ok", []
        elif u.path == "/api/v1/reviews":
            from .api.reviews import CONFIDENCE_BUCKETS, REVIEW_STATUSES, build_reviews
            status = enum_value(query, "status", REVIEW_STATUSES, default="pending")
            confidence = enum_value(
                query, "confidence", CONFIDENCE_BUCKETS, default="all",
            )
            platform = query_value(query, "platform")
            limit = query_value(query, "limit")
            cursor = query_value(query, "cursor")
            store = app.Store(db)
            try:
                data, quality, quality_notes = build_reviews(
                    store, watch, entity_id=requested_entity, status=status,
                    platform=platform, confidence=confidence, limit=limit, cursor=cursor,
                )
            finally:
                store.close()
        elif u.path in {"/api/v1/overview", "/api/v1/analysis"}:
            range_name = enum_value(query, "range", RANGES, default="7d")
            store = app.Store(db)
            try:
                if u.path == "/api/v1/overview":
                    data, quality, quality_notes = build_overview(
                        store, watch, entity_id=requested_entity, range_name=range_name,
                    )
                else:
                    from .api.analysis import build_analysis
                    data, quality, quality_notes = build_analysis(
                        store, watch, entity_id=requested_entity, range_name=range_name,
                    )
            finally:
                store.close()
        elif u.path == "/api/v1/collection/status":
            from . import login
            from .api.collection import build_collection_status
            include_login = enum_value(query, "include_login", ("0", "1"), default="1") == "1"

            def _login_provider(platforms):
                return login.bridge_ok(), login.status(platforms)

            store = app.Store(db)
            try:
                data, quality, quality_notes = build_collection_status(
                    store, watch, dict(app._run_state), entity_id=requested_entity,
                    login_provider=_login_provider if include_login else None,
                )
            finally:
                store.close()
        elif u.path == "/api/v1/collection/login-status":
            from . import login
            from .api.collection import execution_environment
            resolved_id, entity_name = resolve_entity(watch, requested_entity)
            platforms = [str(item) for item in (watch.get("platforms") or [])]
            bridge_ok, bridge_message = login.bridge_ok()
            data = {
                "entity": {"id": resolved_id, "name": entity_name},
                "execution": execution_environment(),
                "bridge": {"ok": bridge_ok, "message": bridge_message},
                "platforms": login.status(platforms),
            }
            quality = "ok" if bridge_ok else "degraded"
            quality_notes = [] if bridge_ok else [bridge_message]
        elif u.path in {"/api/v1/backlog", "/api/v1/backlog.csv"}:
            from .api.backlog import backlog_csv as build_backlog_csv, build_backlog
            range_name = enum_value(query, "range", RANGES, default="30d")
            if u.path == "/api/v1/backlog.csv":
                store = app.Store(db)
                try:
                    csv_text, csv_entity_id = build_backlog_csv(
                        store, watch, entity_id=requested_entity, range_name=range_name,
                    )
                finally:
                    store.close()
                data = {"_csv": csv_text, "entity": {"id": csv_entity_id}}
                quality, quality_notes = "ok", []
            else:
                store = app.Store(db)
                try:
                    data, quality, quality_notes = build_backlog(
                        store, watch, entity_id=requested_entity, range_name=range_name,
                    )
                finally:
                    store.close()
        elif u.path == "/api/v1/reports" or report_match is not None or document_match is not None:
            from .api.reports import (
                build_report_detail, build_report_list, build_source_document,
            )
            store = app.Store(db)
            try:
                if report_match is not None:
                    data, quality, quality_notes = build_report_detail(
                        store, watch, unquote(report_match.group(1)),
                        entity_id=requested_entity,
                    )
                elif document_match is not None:
                    data, quality, quality_notes = build_source_document(
                        store, watch, document_match.group(1),
                        entity_id=requested_entity,
                    )
                else:
                    data, quality, quality_notes = build_report_list(
                        store, watch, entity_id=requested_entity,
                    )
            finally:
                store.close()
        else:
            from .api.incidents import build_incident_detail, build_incident_list
            store = app.Store(db)
            try:
                if incident_match is not None:
                    data, quality, quality_notes = build_incident_detail(
                        store, watch, incident_match.group(1),
                    )
                else:
                    status = query_value(query, "status")
                    data, quality, quality_notes = build_incident_list(
                        store, watch, entity_id=requested_entity, status=status,
                    )
            finally:
                store.close()
    except APIError as exc:
        self._send_api_error(exc.code, exc.message, exc.status)
        return
    except (SystemExit, OSError, ValueError, TypeError):
        self._send_api_error("CONFIG_ERROR", "监控配置无法读取", 503)
        return
    except Exception:
        self._send_api_error("INTERNAL_ERROR", "服务暂时不可用", 500)
        return

    if u.path == "/api/v1/backlog.csv":
        csv_data = data["_csv"].encode("utf-8-sig")
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", 'attachment; filename="yuqing-backlog.csv"')
        self.send_header("Content-Length", str(len(csv_data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(csv_data)
        return
    self._send_json(success_payload(
        data,
        entity_id=data["entity"]["id"],
        data_quality=quality,
        quality_notes=quality_notes,
    ))



def handle_post(self, u, db: str, app) -> None:
    from .api.collection import execution_environment
    from .api.overview import resolve_entity
    from .api.responses import APIError, json_body, success_payload

    if not self._api_mutation_allowed():
        self._send_api_error("FORBIDDEN", "无权执行该操作", 403)
        return
    incident_match = re.fullmatch(r"/api/v1/incidents/([0-9A-Za-z_-]+)/transition", u.path)
    review_match = (None if u.path == "/api/v1/reviews/batch" else
                    re.fullmatch(r"/api/v1/reviews/([0-9A-Za-z_-]+)", u.path))
    if u.path not in {
        "/api/v1/collection/run", "/api/v1/collection/stop", "/api/v1/reviews/batch",
        "/api/v1/collection/login/open", "/api/v1/reports/generate",
        "/api/v1/keywords", "/api/v1/seeds",
    } and incident_match is None and review_match is None:
        self._send_api_error("NOT_FOUND", "接口不存在", 404)
        return
    try:
        from . import load_watch
        watch = load_watch()
        entity_id, _ = resolve_entity(watch, None)
    except APIError as exc:
        self._send_api_error(exc.code, exc.message, exc.status)
        return
    except (SystemExit, OSError, ValueError, TypeError):
        self._send_api_error("CONFIG_ERROR", "监控配置无法读取", 503)
        return

    if review_match is not None or u.path == "/api/v1/reviews/batch":
        from .api.reviews import save_review, save_review_batch
        try:
            body = json_body(self)
            requested_entity = str(body.get("entity_id") or "").strip() or None
            resolved_entity_id, _ = resolve_entity(watch, requested_entity)
            principal = self._api_principal() or {}
            actor = principal.get("name") or principal.get("open_id") or "unknown"
            store = app.Store(db)
            try:
                if review_match is not None:
                    review = save_review(
                        store, watch, review_match.group(1),
                        verdict=str(body.get("verdict") or "").strip(),
                        entity_id=requested_entity,
                        note=str(body.get("note") or ""),
                        actor=actor,
                    )
                    data = {"review": review}
                else:
                    data = save_review_batch(
                        store, watch, body.get("items"),
                        entity_id=requested_entity, actor=actor,
                    )
            finally:
                store.close()
        except APIError as exc:
            self._send_api_error(exc.code, exc.message, exc.status)
            return
        except Exception:
            self._send_api_error("INTERNAL_ERROR", "服务暂时不可用", 500)
            return
        self._send_json(success_payload(
            data, entity_id=resolved_entity_id, data_quality="ok",
        ))
        return

    if incident_match is not None:
        from . import alerts
        from .api.incidents import allowed_action_names, serialize_incident
        try:
            body = json_body(self)
            action = str(body.get("action") or "").strip()
            note = str(body.get("note") or "").strip()[:1000]
            store = app.Store(db)
            try:
                incident = store.get_incident(incident_match.group(1))
                if incident is None:
                    raise APIError("NOT_FOUND", "事件不存在", 404)
                if action not in allowed_action_names(incident):
                    raise APIError("INVALID_TRANSITION", "当前状态不能执行该操作", 409)
                principal = self._api_principal() or {}
                actor = principal.get("name") or principal.get("open_id") or "unknown"
                now = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
                result = alerts.transition(
                    store, incident["incident_id"], action, actor=actor, now=now, note=note,
                )
                if not result.get("success"):
                    code = "DELIVERY_FAILED" if action == "escalate" else "INVALID_TRANSITION"
                    raise APIError(code, result.get("message") or "事件状态更新失败", 409)
                data = {
                    "incident": serialize_incident(result["incident"]),
                    "executive_pushed": bool(result.get("executive_pushed")),
                }
            finally:
                store.close()
        except APIError as exc:
            self._send_api_error(exc.code, exc.message, exc.status)
            return
        except Exception:
            self._send_api_error("INTERNAL_ERROR", "服务暂时不可用", 500)
            return
        self._send_json(success_payload(data, entity_id=entity_id, data_quality="ok"))
        return
    if u.path == "/api/v1/collection/login/open":
        from . import login
        try:
            platform = str(json_body(self).get("platform") or "").strip()
            if platform not in login.LOGIN_URLS:
                raise APIError("INVALID_PARAMETER", "该平台不支持交互登录", 400)
            message = login.open_login(platform)
        except APIError as exc:
            self._send_api_error(exc.code, exc.message, exc.status)
            return
        except Exception as exc:
            self._send_api_error("COLLECTOR_UNAVAILABLE", str(exc)[:200], 409)
            return
        self._send_json(success_payload(
            {"platform": platform, "message": message or "已打开登录页"},
            entity_id=entity_id, data_quality="ok",
        ))
        return
    if u.path in {"/api/v1/keywords", "/api/v1/seeds"}:
        from .api.watch import mutate_keyword, mutate_seed
        try:
            body = json_body(self)
            requested_entity = str(body.get("entity_id") or "").strip() or None
            resolved_entity_id, _ = resolve_entity(watch, requested_entity)
            store = app.Store(db)
            try:
                if u.path == "/api/v1/keywords":
                    result = mutate_keyword(
                        store, watch, body, entity_id=requested_entity,
                    )
                else:
                    result, watch = mutate_seed(
                        store, watch, body, entity_id=requested_entity,
                    )
            finally:
                store.close()
        except APIError as exc:
            self._send_api_error(exc.code, exc.message, exc.status)
            return
        except Exception:
            self._send_api_error("INTERNAL_ERROR", "监控配置操作失败", 500)
            return
        self._send_json(success_payload(
            {"entity": {"id": resolved_entity_id}, "result": result},
            entity_id=resolved_entity_id, data_quality="ok",
        ))
        return
    if u.path == "/api/v1/reports/generate":
        from .api.reports import generate_report
        try:
            body = json_body(self)
            requested_entity = str(body.get("entity_id") or "").strip() or None
            store = app.Store(db)
            try:
                data, quality, quality_notes = generate_report(
                    store, watch, entity_id=requested_entity,
                )
            finally:
                store.close()
        except APIError as exc:
            self._send_api_error(exc.code, exc.message, exc.status)
            return
        except Exception:
            self._send_api_error("INTERNAL_ERROR", "报告生成失败", 500)
            return
        self._send_json(success_payload(
            data, entity_id=data["entity"]["id"], data_quality=quality,
            quality_notes=quality_notes,
        ), 201)
        return
    if u.path == "/api/v1/collection/run":
        execution = execution_environment()
        if not execution["can_run"]:
            self._send_api_error("COLLECTION_UNAVAILABLE", execution["message"], 409)
            return
        result = app._start_background_run(db)
    else:
        result = app._request_run_stop()
        if not result["stop_requested"]:
            self._send_api_error("NOT_RUNNING", result["message"], 409)
            return
    self._send_json(success_payload(result, entity_id=entity_id, data_quality="ok"))



def handle_put(self, u, db: str, app) -> None:
    from .api.responses import APIError, json_body, success_payload

    if u.path != "/api/v1/watch":
        self._send_api_error("NOT_FOUND", "接口不存在", 404)
        return
    if not self._api_mutation_allowed():
        self._send_api_error("FORBIDDEN", "无权执行该操作", 403)
        return
    try:
        from . import load_watch
        from .api.overview import resolve_entity
        from .api.watch import build_watch_config, update_watch_config
        body = json_body(self)
        current = load_watch()
        updated = update_watch_config(current, body)
        requested_entity = str(body.get("entity_id") or "").strip() or None
        entity_id, _ = resolve_entity(updated, requested_entity)
        data = build_watch_config(updated, entity_id=entity_id)
    except APIError as exc:
        self._send_api_error(exc.code, exc.message, exc.status)
        return
    except Exception:
        self._send_api_error("CONFIG_WRITE_FAILED", "监控配置保存失败", 500)
        return
    self._send_json(success_payload(data, entity_id=entity_id, data_quality="ok"))
