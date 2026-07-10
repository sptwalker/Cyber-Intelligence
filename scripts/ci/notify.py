#!/usr/bin/env python3
"""Send Cyber-Intelligence CCE deployment status to Feishu."""
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

CHAT_ID = "oc_aae2fdb8d29cc64e86efa7ce6c0e60da"
TZ = timezone(timedelta(hours=8))


def _env(name: str) -> str:
    return os.environ.get(name, "")


def _completed_at() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")


def _card_header(mode: str) -> tuple[str, str]:
    pid = _env("CI_PIPELINE_ID")
    if mode == "success":
        return f"✅ Cyber 部署成功 #{pid}", "green"
    return f"❌ Cyber 部署失败 #{pid}", "red"


def _card_content() -> str:
    return "\n".join(
        [
            f"**流水线**：{_env('CI_PIPELINE_URL')}",
            f"**分支**：{_env('CI_COMMIT_BRANCH')}",
            f"**提交**：{_env('CI_COMMIT_SHORT_SHA')} {_env('CI_COMMIT_TITLE')}",
            f"**触发者**：{_env('GITLAB_USER_NAME')}",
            f"**完成时间**：{_completed_at()}",
            "**地址**：[cyber.youdoogo.com](https://cyber.youdoogo.com)",
        ]
    )


def _feishu_api_send(mode: str) -> int:
    app_id = _env("FEISHU_APP_ID")
    app_secret = _env("FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        print("[notify] FEISHU_APP_ID or FEISHU_APP_SECRET is not configured", file=sys.stderr)
        return 2

    token_req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        data=json.dumps({"app_id": app_id, "app_secret": app_secret}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(token_req, timeout=10) as resp:
            token_body = json.load(resp)
    except Exception as exc:
        print(f"[notify] token fetch error: {exc}", file=sys.stderr)
        return 2
    token = str(token_body.get("tenant_access_token", ""))
    if not token:
        print(f"[notify] token empty: {json.dumps(token_body, ensure_ascii=False)[:200]}", file=sys.stderr)
        return 2

    title, template = _card_header(mode)
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": template,
        },
        "elements": [{"tag": "markdown", "content": _card_content()}],
    }
    payload = {
        "receive_id": CHAT_ID,
        "msg_type": "interactive",
        "content": json.dumps(card, ensure_ascii=False),
    }
    send_req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(send_req, timeout=15) as resp:
            body = json.load(resp)
    except Exception as exc:
        print(f"[notify] send error: {exc}", file=sys.stderr)
        return 2
    if body.get("code") == 0:
        print(f"[notify] Feishu OK (msg_id={body.get('data', {}).get('message_id', '')})")
        return 0
    print(f"[notify] Feishu FAILED code={body.get('code')} msg={body.get('msg', '')}", file=sys.stderr)
    return 2


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode not in ("success", "failure"):
        print(f"usage: {sys.argv[0]} success|failure", file=sys.stderr)
        return 1
    print(f"[notify] mode={mode} pipeline={_env('CI_PIPELINE_ID')} chat={CHAT_ID}")
    return _feishu_api_send(mode)


if __name__ == "__main__":
    raise SystemExit(main())
