# -*- coding: utf-8 -*-
"""External collector adapters and the Heimao browser bridge."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable, Mapping
from urllib.parse import quote


# Windows 上 opencli 是 .CMD 脚本无 .exe，subprocess 裸名 CreateProcess 找不到（不套 PATHEXT）。
# 用 shutil.which 解析全路径；mac/Linux 返回普通路径，同样正确。
OPENCLI_BINARY = shutil.which("opencli") or "opencli"

# 平台名 → opencli site。黑猫无 opencli 后端，走登录态浏览器桥。
OPENCLI_SITE = {
    "weibo": "weibo",
    "zhihu": "zhihu",
    "douyin": "douyin",
    "xiaohongshu": "xiaohongshu",
    "bilibili": "bilibili",
    "tieba": "tieba",
    "weixin": "weixin",
    "hupu": "hupu",
    "smzdm": "smzdm",
}

# 锚定投诉详情链接里的 >=6 位 id。真实 markdown 里 URL 可能是协议相对地址并带 query。
HEIMAO_LINK = re.compile(r"(?:https?:)?//tousu\.sina\.com\.cn/complaint/view/(\d{6,})")


def parse_opencli_json(stdout: str, returncode: int, site: str, limit: int) -> list[dict]:
    """Parse opencli output while distinguishing an empty result from failure."""
    data = json.loads(stdout or "[]")
    if isinstance(data, dict) and data.get("ok") is False:
        code = (data.get("error") or {}).get("code", "")
        if code in ("NOT_FOUND", "EMPTY", "NO_RESULTS"):
            return []
        message = (data.get("error") or {}).get("message", "") or code
        raise RuntimeError(f"opencli {site} 失败({code}): {message[:160]}")
    if returncode != 0 and not isinstance(data, (list, dict)):
        raise RuntimeError(f"opencli {site} 退出码 {returncode}")
    items = data if isinstance(data, list) else data.get("items") or data.get("data") or []
    return items[:limit]


def fetch_opencli(
    platform: str,
    keyword: str,
    limit: int,
    *,
    opencli: str = OPENCLI_BINARY,
    opencli_sites: Mapping[str, str] = OPENCLI_SITE,
    run_command: Callable = subprocess.run,
    parse_json: Callable[[str, int, str, int], list[dict]] = parse_opencli_json,
) -> list[dict]:
    """Fetch one search query through the sidecar or a local opencli adapter."""
    from .. import collector_client

    if collector_client.enabled():
        return collector_client.fetch(platform, keyword, limit)
    site = opencli_sites.get(platform)
    if not site:
        raise ValueError(f"平台 {platform} 无 opencli 后端，请走 Web/Jina 或提供 fixture")
    output = run_command(
        [opencli, site, "search", keyword, "--limit", str(min(limit, 50)), "-f", "json"],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    return parse_json(output.stdout, output.returncode, site, limit)


def fetch_opencli_userposts(
    site: str,
    user: str,
    limit: int,
    *,
    opencli: str = OPENCLI_BINARY,
    run_command: Callable = subprocess.run,
) -> list[dict]:
    """Fetch a tracked KOL/official-account timeline."""
    from .. import collector_client

    if collector_client.enabled():
        return collector_client.fetch(site, "", limit, entry="user-posts", user=user)
    output = run_command(
        [opencli, site, "user-posts", user, "-f", "json"],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    if output.returncode != 0:
        raise RuntimeError(f"opencli {site} user-posts 失败: {output.stderr[:200]}")
    data = json.loads(output.stdout or "[]")
    return (data if isinstance(data, list) else data.get("items") or data.get("data") or [])[:limit]


def parse_heimao_markdown(
    markdown: str, *, link_pattern=HEIMAO_LINK,
) -> list[dict]:
    """Extract de-duplicated complaint items from browser-rendered markdown."""
    markdown = markdown or ""
    seen: set[str] = set()
    items: list[dict] = []
    previous_end = 0
    for match in link_pattern.finditer(markdown):
        complaint_id = match.group(1)
        segment = markdown[previous_end:match.start()]
        previous_end = match.end()
        if complaint_id in seen:
            continue
        seen.add(complaint_id)
        text = segment.replace("\\n", " ").replace("\\", "")
        text = re.sub(r"[\n\r]+", " ", text)
        text = re.sub(r"[\[\]()*#>-]", " ", text)
        text = " ".join(text.split())[-140:].strip()
        items.append({
            "id": complaint_id,
            "text": text or f"投诉{complaint_id}",
            "url": f"https://tousu.sina.com.cn/complaint/view/{complaint_id}/",
        })
    return items


def opencli_browser(
    session: str,
    *args: str,
    timeout: int = 60,
    opencli: str = OPENCLI_BINARY,
    run_command: Callable = subprocess.run,
) -> str:
    """Run one command against the bound browser session."""
    output = run_command(
        [opencli, "browser", session, *args],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if output.returncode != 0:
        raise RuntimeError(
            f"opencli browser {' '.join(args)[:40]} 失败: {(output.stderr or '')[:200]}"
        )
    return output.stdout or ""


def heimao_is_login_wall(markdown: str) -> bool:
    """Return whether rendered Heimao content lacks the logged-in logout marker."""
    return "退出" not in (markdown or "")


def fetch_heimao(
    keyword: str,
    limit: int,
    *,
    pages: int = 1,
    session: str | None = None,
    browser_call: Callable[..., str] = opencli_browser,
    parse_markdown: Callable[[str], list[dict]] = parse_heimao_markdown,
    login_wall: Callable[[str], bool] = heimao_is_login_wall,
    sleep: Callable[[float], None] = time.sleep,
    link_pattern=HEIMAO_LINK,
) -> list[dict]:
    """Fetch Heimao search results through the authenticated browser bridge."""
    from .. import collector_client

    if collector_client.enabled():
        return collector_client.fetch("heimao", keyword, limit)
    session = session or os.getenv("YUQING_OPENCLI_SESSION", "yuqing")
    items: list[dict] = []
    seen: set[str] = set()
    markdown = ""
    for page in range(1, pages + 1):
        url = f"https://tousu.sina.com.cn/index/search/?keywords={quote(keyword)}"
        if page > 1:
            url += f"&page={page}"
        browser_call(session, "open", url)
        markdown = ""
        for _ in range(4):
            sleep(1.5)
            markdown = browser_call(session, "extract")
            if link_pattern.search(markdown) or "退出" in markdown:
                break
        for item in parse_markdown(markdown):
            if item["id"] not in seen:
                seen.add(item["id"])
                items.append(item)
        if len(items) >= limit:
            break
    if not items and login_wall(markdown):
        raise RuntimeError("黑猫登录态失效（tousu.sina.com.cn 出现登录墙），请重新登录")
    return items[:limit]
