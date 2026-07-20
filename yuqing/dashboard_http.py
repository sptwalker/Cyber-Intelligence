# -*- coding: utf-8 -*-
"""HTTP adapter for dashboard assets, legacy routes, auth flow, and API delegation."""

from __future__ import annotations

import datetime as _dt
import json
import mimetypes
import re
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

def make_handler(db: str, app):
    class Handler(BaseHTTPRequestHandler):
        def end_headers(self):
            """Keep legacy JSON APIs available while advertising the versioned successor."""
            path = urlparse(getattr(self, "path", "")).path
            if path.startswith("/api/") and not path.startswith("/api/v1/"):
                self.send_header("Deprecation", "true")
                self.send_header("Link", '</api/v1>; rel="successor-version"')
            super().end_headers()

        def _send(self, body: str, code: int = 200):
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_bytes(self, data: bytes, content_type: str, code: int = 200):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def _send_workbench_asset(self, asset_name: str) -> None:
            """Serve one packaged asset without allowing path traversal."""
            decoded = unquote(asset_name)
            relative = Path(decoded)
            if (not decoded or "\x00" in decoded or relative.is_absolute()
                    or decoded.startswith(("/", "\\"))):
                self.send_error(404)
                return
            root = app._WORKBENCH_DIR.resolve()
            candidate = (root / relative).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                self.send_error(404)
                return
            if not candidate.is_file():
                self.send_error(404)
                return
            content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
            if content_type.startswith("text/") or content_type in ("application/javascript", "application/json"):
                content_type += "; charset=utf-8"
            self._send_bytes(candidate.read_bytes(), content_type)

        def _send_json(self, payload: dict, code: int = 200):
            data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def _send_api_error(self, code: str, message: str, status: int):
            from .api.responses import error_payload
            self._send_json(error_payload(code, message), status)

        def _api_principal(self) -> dict | None:
            """Return one reusable identity shape for local and OAuth API reads."""
            if app._write_allowed(self):
                return {"open_id": "local", "name": "本机用户", "auth_type": "local"}
            user = app._require_auth(self)
            return ({**user, "auth_type": "oauth"} if user else None)

        def _api_mutation_allowed(self) -> bool:
            """Expose the existing session/origin/forwarded-host checks to v1 routes."""
            return app._mutation_allowed(self)

        def _handle_api_v1_get(self, u) -> None:
            from .dashboard_api_v1 import handle_get
            handle_get(self, u, db, app)

        def _handle_api_v1_post(self, u) -> None:
            from .dashboard_api_v1 import handle_post
            handle_post(self, u, db, app)

        def _handle_api_v1_put(self, u) -> None:
            from .dashboard_api_v1 import handle_put
            handle_put(self, u, db, app)

        def _redirect(self, location: str, set_cookie: str = ""):
            """302 跳转，可带 Set-Cookie（登录建会话/登出清会话）。"""
            self.send_response(302)
            self.send_header("Location", location)
            if set_cookie:
                self.send_header("Set-Cookie", set_cookie)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _auth_login(self):
            """/auth/login：生成 state → 302 跳飞书授权页。未配置 App 则友好提示（不 500）。"""
            from . import config
            u = urlparse(self.path)
            nxt = parse_qs(u.query).get("next", ["/"])[0]
            app_id = config.resolve("FEISHU_APP_ID")
            redirect_uri = config.resolve("FEISHU_REDIRECT_URI")
            if not app_id or not redirect_uri:          # DoD#3：缺配置给提示页，不报 500
                self._send(app.render_auth_hint()); return
            state = app._new_state(nxt)
            self._redirect(app._feishu_authorize_url(app_id, redirect_uri, state))

        def _auth_callback(self):
            """/auth/callback：校验 state → code 换 token → 取用户信息 → 建会话 → 302 回原始路径。"""
            q = parse_qs(urlparse(self.path).query)
            code = q.get("code", [""])[0]
            state = q.get("state", [""])[0]
            st = app._oauth_states.pop(state, None)         # state 一次性消费（防 CSRF/重放）
            if not code or not st:
                self._send(app.render_auth_error("授权校验失败（state 无效或已过期），请重新登录。"), 400)
                return
            try:
                token = app._feishu_user_access_token(code)
                user = app._feishu_user_info(token)
            except Exception as e:                      # 飞书 API 异常也不 500，给可读提示
                self._send(app.render_auth_error(f"飞书登录失败：{str(e)[:200]}"), 502); return
            sid = app._new_session(user)
            self._redirect(app._safe_next(st.get("next", "/")), app._cookie_header(sid))

        def _auth_logout(self):
            """/auth/logout：清会话 + 过期 cookie → 302 回登录页。"""
            sid = app._sid_from_cookie(self)
            if sid:
                app._session_delete(sid)
            self._redirect("/auth/login", app._cookie_header("", clear=True))

        def do_GET(self):
            u = urlparse(self.path)
            # /auth/* 是登录流程页，本身无需登录
            if u.path == "/auth/login":
                self._auth_login(); return
            if u.path == "/auth/callback":
                self._auth_callback(); return
            if u.path == "/auth/logout":
                self._auth_logout(); return
            # /config 维持原有本机保护（SSH 隧道用），不走飞书 OAuth
            if u.path == "/config":
                if not app._write_allowed(self):
                    self.send_error(403); return
                self._send(app.render_config()); return
            if u.path == "/config/test":
                if not app._write_allowed(self):
                    self.send_error(403); return
                self._send(app.render_config(test_msg=app._run_test(parse_qs(u.query).get("p", [""])[0]))); return
            if u.path.startswith("/api/v1/"):
                self._handle_api_v1_get(u); return
            if u.path in ("/", "/v2", "/v2/"):
                if not app._write_allowed(self) and app._require_auth(self) is None:
                    self._redirect(f"/auth/login?next={quote(self.path, safe='')}"); return
                self._send_workbench_asset("index.html")
                return
            asset_prefix = next(
                (prefix for prefix in ("/assets/", "/v2/assets/") if u.path.startswith(prefix)),
                None,
            )
            if asset_prefix is not None:
                if self._api_principal() is None:
                    self.send_error(401); return
                self._send_workbench_asset(u.path[len(asset_prefix):])
                return
            if u.path == "/api/run/status":
                if not (app._write_allowed(self) or app._require_auth(self)):
                    self.send_error(403); return
                payload = json.dumps({"running": app._run_state["running"], "last": app._run_state["last"],
                                      "current": app._run_state["current"]},
                                     ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload); return
            if u.path == "/api/login/status":
                if not app._write_allowed(self):
                    self.send_error(403); return
                from . import login, load_watch
                try:
                    platforms = load_watch().get("platforms", [])
                except SystemExit:
                    platforms = list(login.LOGIN_URLS)
                ok, msg = login.bridge_ok()
                payload = json.dumps({"bridge_ok": ok, "bridge_msg": msg,
                                      "platforms": login.status(platforms)},
                                     ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload); return
            if u.path == "/login":
                if not app._write_allowed(self):
                    self.send_error(403); return
                self._send(app.render_login()); return
            if u.path == "/watch":
                if not app._write_allowed(self):
                    self.send_error(403); return
                self._send(app.render_watch()); return
            # 本机保持零配置可用；远程访问必须飞书 OAuth 登录。
            if not app._write_allowed(self) and app._require_auth(self) is None:
                self._redirect(f"/auth/login?next={quote(self.path, safe='')}"); return
            store = app.Store(db)
            try:
                if u.path == "/legacy":
                    body = app.render_index(store)
                elif u.path == "/exec":
                    body = app.render_exec(store)
                elif u.path == "/dash":
                    body = app.render_dash(store, parse_qs(u.query).get("entity", [""])[0])
                elif u.path == "/chart-data":
                    from . import load_watch
                    try:
                        w = load_watch()
                    except SystemExit:
                        w = None
                    payload = json.dumps(app.chart_data(store, parse_qs(u.query).get("entity", [""])[0], w),
                                         ensure_ascii=False).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload); return
                elif u.path == "/report":
                    body = app.render_report(store, parse_qs(u.query).get("run_id", [""])[0])
                elif u.path == "/keywords":
                    body = app.render_keywords(store, parse_qs(u.query))
                elif u.path == "/annotate":
                    body = app.render_annotate(store, parse_qs(u.query))
                elif u.path == "/accounts":
                    body = app.render_accounts(store)
                elif u.path == "/api/seed/list":
                    from .keywords import KeywordManager
                    km = KeywordManager(store)
                    entity_id = parse_qs(u.query).get("entity", [None])[0]
                    seeds = km.list_suggestions(status='pending', entity_id=entity_id, tag='seed_alias')
                    payload = json.dumps({"seeds": seeds}, ensure_ascii=False, default=str).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload); return
                elif u.path == "/api/annotate/queue":
                    from . import analytics
                    entity_id = parse_qs(u.query).get("entity", [None])[0]
                    queue = analytics.active_sample(store, entity_id, limit=20)
                    payload = json.dumps({"queue": queue}, ensure_ascii=False, default=str).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload); return
                elif u.path == "/api/keywords":
                    # API: 返回JSON
                    from .keywords import KeywordManager
                    km = KeywordManager(store)
                    tag = parse_qs(u.query).get("tag", [None])[0]
                    entity_id = parse_qs(u.query).get("entity", [None])[0]
                    keywords = km.list(tag=tag, entity_id=entity_id)
                    suggestions = km.list_suggestions(status='pending', entity_id=entity_id, exclude_tag='seed_alias')
                    payload = json.dumps({
                        'keywords': keywords,
                        'suggestions': suggestions
                    }, ensure_ascii=False, default=str).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload); return
                elif u.path == "/api/incidents":
                    status = parse_qs(u.query).get("status", [None])[0]
                    payload = json.dumps({"incidents": store.list_incidents(status=status)},
                                         ensure_ascii=False, default=str).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload); return
                else:
                    self.send_error(404); return
            finally:
                store.close()
            self._send(body)

        def do_POST(self):
            u = urlparse(self.path)
            if u.path.startswith("/api/v1/"):
                self._handle_api_v1_post(u); return
            if u.path == "/config":
                if not app._write_allowed(self):
                    self.send_error(403); return
                from . import config
                n = int(self.headers.get("Content-Length") or 0)
                form = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode("utf-8")).items()}
                config.save(form)
                self._send(app.render_config(saved=True))
            elif u.path == "/api/run":
                # 触发一次跑批（collect→analyze→report），后台线程执行，防并发
                if not app._mutation_allowed(self):
                    self.send_error(403); return
                result = app._start_background_run(db)
                payload = json.dumps(result, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            elif u.path == "/api/run/stop":
                # 协作式停止：置标志，采集在下个平台边界中止（已采数据保留）
                if not app._mutation_allowed(self):
                    self.send_error(403); return
                stop_result = app._request_run_stop()
                result = {"success": stop_result["stop_requested"], "message": stop_result["message"]}
                payload = json.dumps(result, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            elif u.path == "/api/login/open":
                # 在桥接 Chrome 打开某平台登录页（platform 白名单校验，防注入）
                if not app._write_allowed(self):
                    self.send_error(403); return
                from . import login
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n).decode("utf-8")
                platform = (json.loads(raw).get("platform") if raw.startswith("{")
                            else parse_qs(raw).get("platform", [""])[0])
                try:
                    login.open_login(platform)
                    result = {"success": True, "message": f"已在浏览器打开 {platform} 登录页，请登录后点重新检测"}
                except Exception as e:
                    result = {"success": False, "message": str(e)[:200]}
                payload = json.dumps(result, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            elif u.path == "/api/watch":
                # 保存 watch.yaml：强校验 → 写前备份 .bak → 覆盖。校验不过绝不落盘（护单一事实源）
                if not app._write_allowed(self):
                    self.send_error(403); return
                from . import watch_path
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n).decode("utf-8")
                try:
                    content = json.loads(raw).get("content", "") if raw.startswith("{") else ""
                except Exception:
                    content = ""
                ok, msg = app._validate_watch(content)
                if ok:
                    try:
                        import shutil
                        p = watch_path()
                        try:
                            shutil.copyfile(p, p + ".bak")     # 覆盖前备份，防手滑丢配置
                        except FileNotFoundError:
                            pass
                        with open(p, "w", encoding="utf-8") as f:
                            f.write(content)
                        result = {"success": True, "message": msg + "，已保存（下轮采集/刷新生效）"}
                    except Exception as e:
                        result = {"success": False, "message": f"写入失败：{str(e)[:200]}"}
                else:
                    result = {"success": False, "message": msg + "（未保存）"}
                payload = json.dumps(result, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            elif u.path == "/api/keywords":
                # 关键词API：添加/删除/审核
                if not app._mutation_allowed(self):
                    self.send_error(403); return
                store = app.Store(db)
                try:
                    from .keywords import KeywordManager
                    km = KeywordManager(store)
                    n = int(self.headers.get("Content-Length") or 0)
                    body_data = self.rfile.read(n).decode("utf-8")
                    data = json.loads(body_data) if body_data.startswith('{') else parse_qs(body_data)

                    # 处理表单格式
                    if isinstance(data, dict) and not isinstance(list(data.values())[0], list):
                        form = data
                    else:
                        form = {k: v[0] for k, v in data.items()}

                    action = form.get('action', '')
                    result = {'success': False, 'message': ''}

                    if action == 'add':
                        try:
                            km.add(
                                word=form['word'],
                                tag=form['tag'],
                                entity_id=form.get('entity_id') or None,
                                weight=float(form.get('weight', 1.0)),
                                note=form.get('note', '')
                            )
                            result = {'success': True, 'message': '添加成功'}
                        except Exception as e:
                            result = {'success': False, 'message': str(e)}

                    elif action == 'delete':
                        success = km.remove(
                            word=form['word'],
                            tag=form['tag'],
                            entity_id=form.get('entity_id') or None
                        )
                        result = {'success': success, 'message': '删除成功' if success else '未找到'}

                    elif action == 'approve':
                        success = km.approve_suggestion(int(form['id']))
                        result = {'success': success, 'message': '已批准' if success else '失败'}

                    elif action == 'reject':
                        success = km.reject_suggestion(int(form['id']))
                        result = {'success': success, 'message': '已拒绝' if success else '失败'}

                    payload = json.dumps(result, ensure_ascii=False).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                finally:
                    store.close()

            elif u.path == "/api/annotate":
                # 多维标注落库 + 圈词进关键词库待审（product_name 额外进种子建议，扩召回）
                if not app._mutation_allowed(self):
                    self.send_error(403); return
                import datetime as _dt2
                store = app.Store(db)
                try:
                    from .keywords import KeywordManager, SUBJECTS, STANCES
                    n = int(self.headers.get("Content-Length") or 0)
                    d = json.loads(self.rfile.read(n).decode("utf-8"))
                    doc_id = d.get("doc_id")
                    subject = d.get("subject") if d.get("subject") in SUBJECTS else None
                    stance = d.get("stance") if d.get("stance") in STANCES else None
                    if not doc_id or not subject or not stance:
                        result = {"success": False, "message": "缺 doc_id/主体/立场，或枚举非法"}
                    else:
                        eid = d.get("entity_id")
                        words = d.get("picked_words") or []
                        now = _dt2.datetime.now().isoformat(timespec="seconds")
                        store.add_annotation(doc_id, subject=subject, stance=stance,
                                             importance=d.get("importance"), picked_words=words,
                                             note=d.get("note", ""), sample_source=d.get("sample_source", "manual"),
                                             entity_id=eid, ts=now)
                        km = KeywordManager(store)
                        for w in words:
                            word, role = (w.get("word") or "").strip(), w.get("role") or "related"
                            if not word:
                                continue
                            try:                                       # 圈词进判别词待审队列
                                km.add_suggestion(word, role, eid, score=0.9, reason="标注圈选",
                                                  source_docs=json.dumps([doc_id]))
                                if role == "product_name":              # 产品名额外进种子建议（扩召回侧）
                                    km.add_suggestion(word, "seed_alias", eid, score=0.9,
                                                      reason="标注圈选·产品名", source_docs=json.dumps([doc_id]))
                            except Exception:
                                pass                                    # 重复/异常不阻断标注保存
                        result = {"success": True, "message": "已保存"}
                    payload = json.dumps(result, ensure_ascii=False).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                finally:
                    store.close()
            elif u.path == "/api/seed":
                # 种子建议：mine 挖词 / approve 写回 watch.yaml aliases / reject
                if not app._mutation_allowed(self):
                    self.send_error(403); return
                store = app.Store(db)
                try:
                    from .keywords import KeywordManager
                    from . import analytics, load_watch
                    n = int(self.headers.get("Content-Length") or 0)
                    d = json.loads(self.rfile.read(n).decode("utf-8"))
                    act = d.get("action")
                    km = KeywordManager(store)
                    if act == "mine":
                        try:
                            cnt = analytics.mine_and_queue(store, load_watch(), km=km)
                            result = {"success": True, "message": f"挖词完成：种子 {cnt['seed']} · 判别词 {cnt['feature']}"}
                        except Exception as e:
                            result = {"success": False, "message": f"挖词失败：{str(e)[:150]}"}
                    elif act == "approve":
                        sug = next((s for s in km.list_suggestions(status='pending', tag='seed_alias')
                                    if s["id"] == int(d.get("id", 0))), None)
                        if not sug:
                            result = {"success": False, "message": "建议不存在"}
                        else:
                            ok, msg = analytics.append_alias(sug["entity_id"], sug["word"])
                            if ok:
                                km.mark_suggestion(sug["id"], "approved")
                            result = {"success": ok, "message": msg}
                    elif act == "reject":
                        ok = km.reject_suggestion(int(d.get("id", 0)))
                        result = {"success": ok, "message": "已忽略" if ok else "失败"}
                    else:
                        result = {"success": False, "message": "未知操作"}
                    payload = json.dumps(result, ensure_ascii=False).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                finally:
                    store.close()
            elif u.path == "/api/accounts":
                # 官方账号白名单：add / delete
                if not app._mutation_allowed(self):
                    self.send_error(403); return
                import datetime as _dt3
                store = app.Store(db)
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                    d = json.loads(self.rfile.read(n).decode("utf-8"))
                    act = d.get("action")
                    if act == "add" and (d.get("author") or "").strip() and d.get("subject_type") in ("官方", "准官方", "媒体"):
                        store.add_account(d["author"].strip(), d["subject_type"],
                                          platform=(d.get("platform") or "").strip(),
                                          note="", ts=_dt3.datetime.now().isoformat(timespec="seconds"))
                        result = {"success": True, "message": "已添加"}
                    elif act == "delete":
                        result = {"success": store.delete_account(int(d.get("id", 0))), "message": "已删除"}
                    else:
                        result = {"success": False, "message": "参数不合法（账号必填，类型须官方/准官方/媒体）"}
                    payload = json.dumps(result, ensure_ascii=False).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                finally:
                    store.close()
            elif u.path == "/api/incidents":
                if not app._mutation_allowed(self):
                    self.send_error(403); return
                store = app.Store(db)
                try:
                    from . import alerts as _alerts
                    n = int(self.headers.get("Content-Length") or 0)
                    d = json.loads(self.rfile.read(n).decode("utf-8"))
                    user = app._require_auth(self) or {}
                    actor = user.get("name") or user.get("open_id") or "local"
                    result = _alerts.transition(
                        store, d.get("incident_id", ""), d.get("action", ""), actor=actor,
                        note=d.get("note", ""), now=_dt.datetime.now().astimezone().isoformat(timespec="seconds"))
                    payload = json.dumps(result, ensure_ascii=False, default=str).encode("utf-8")
                    self.send_response(200 if result.get("success") else 400)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                finally:
                    store.close()
            else:
                self.send_error(404)

        def do_PUT(self):
            u = urlparse(self.path)
            if u.path.startswith("/api/v1/"):
                self._handle_api_v1_put(u); return
            self.send_error(404)

        def log_message(self, *a):  # 静音默认日志
            pass
    return Handler
