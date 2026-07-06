# -*- coding: utf-8 -*-
"""采集层：复用 Agent-Reach / opencli，不写爬虫。

- 在线：subprocess 调 `opencli <site> search "<kw>" -f json`，输出直接归一化。
- 离线：读 fixtures（canned json），让整条链在无登录态/无网络时可跑可测。
职责只到 clean 层（含 is_complaint 词典派生），情绪/topic 留给 analyze。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from urllib.parse import quote
from typing import Optional

from .store import CleanDoc, Store
from . import health
from . import relevance

# Windows 上 opencli 是 .CMD 脚本无 .exe，subprocess 裸名 CreateProcess 找不到（不套 PATHEXT）。
# 用 shutil.which 解析全路径；mac/Linux 返回普通路径，同样正确。
_OPENCLI = shutil.which("opencli") or "opencli"

# 平台名 → opencli site。黑猫(heimao)无 opencli 后端，走 browser 通用桥（见 _fetch_heimao）。
OPENCLI_SITE = {"weibo": "weibo", "zhihu": "zhihu", "douyin": "douyin",
                "xiaohongshu": "xiaohongshu", "bilibili": "bilibili"}

_COMPLAINT_TRIGGERS = ["投诉", "维权", "退款", "退货", "赔偿", "曝光", "避雷", "翻车", "召回", "欺诈"]
_ISO_TS = re.compile(r"^\d{4}-\d{2}-\d{2}")   # 仅 ISO 日期串可参与水位比较


def _pick(d: dict, *keys, default=None):
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return default


def _to_int(v) -> int:
    """'1.2万' / '10w+' / 1234 → int。"""
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v or "0").strip().lower().replace("+", "").replace(",", "")
    try:
        if s.endswith("万") or s.endswith("w"):
            return int(float(s[:-1]) * 10000)
        return int(float(s))
    except ValueError:
        return 0


def _derive_id(item: dict) -> str:
    """取平台原生 id；没有显式 id 字段时（如知乎）从 url 末段/哈希派生稳定 id。"""
    nid = _pick(item, "id", "note_id", "mid", "aweme_id", "rid", default="")
    if nid:
        return str(nid)
    u = _pick(item, "url", "link", "note_url", default="")
    if u:
        seg = u.split("?")[0].rstrip("/").split("/")[-1]
        return seg or hashlib.md5(u.encode("utf-8")).hexdigest()[:12]
    return ""


def normalize(platform: str, entity_id: str, item: dict, backend: str, fetched_at: str) -> CleanDoc:
    text = _pick(item, "text", "content", "desc", "title", default="")
    user = item.get("user") or item.get("author") or {}
    if isinstance(user, str):
        user = {"nickname": user}
    is_complaint = platform == "heimao" or any(t in text for t in _COMPLAINT_TRIGGERS)
    return CleanDoc.build(
        platform=platform, entity_id=entity_id,
        native_id=_derive_id(item),
        text=text,
        author=_pick(user, "nickname", "nick_name", "name", default=""),
        author_followers=_to_int(_pick(user, "followers", "fans", "fans_count", default=0)),
        likes=_to_int(_pick(item, "like_count", "liked_count", "digg_count", "votes", "likes", default=0)),
        comments=_to_int(_pick(item, "comment_count", "comments", "comment", default=0)),
        reposts=_to_int(_pick(item, "repost_count", "share_count", "forward_count", default=0)),
        publish_ts=str(_pick(item, "created_at", "time", "publish_time", "date", default="")),
        url=_pick(item, "url", "link", "note_url", default=""),
        tags=item.get("tags") or item.get("tag_list") or [],
        is_complaint=is_complaint, backend=backend, fetched_at=fetched_at,
    )


def _parse_opencli_json(stdout: str, returncode: int, site: str, limit: int) -> list[dict]:
    """解析 opencli JSON 输出，区分'成功空结果'与'真失败'。

    opencli 对'没搜到'会返回 {ok:false, error:{code:NOT_FOUND}} 且 exitCode=1——
    这是空结果不是故障，必须当 []（否则误判健康三态为 fail=数据不全）。
    真正的登录态/风控失败(NOT_LOGGED_IN 等)才 raise，交给上层记 error。
    """
    data = json.loads(stdout or "[]")
    if isinstance(data, dict) and data.get("ok") is False:
        code = (data.get("error") or {}).get("code", "")
        if code in ("NOT_FOUND", "EMPTY", "NO_RESULTS"):
            return []                                    # 成功的空结果
        msg = (data.get("error") or {}).get("message", "") or code
        raise RuntimeError(f"opencli {site} 失败({code}): {msg[:160]}")
    if returncode != 0 and not isinstance(data, (list, dict)):
        raise RuntimeError(f"opencli {site} 退出码 {returncode}")
    items = data if isinstance(data, list) else data.get("items") or data.get("data") or []
    return items[:limit]


def _fetch_opencli(platform: str, keyword: str, limit: int) -> list[dict]:
    site = OPENCLI_SITE.get(platform)
    if not site:
        raise ValueError(f"平台 {platform} 无 opencli 后端，请走 Web/Jina 或提供 fixture")
    out = subprocess.run(
        [_OPENCLI, site, "search", keyword, "--limit", str(min(limit, 50)), "-f", "json"],
        capture_output=True, encoding="utf-8", errors="replace", timeout=120,
    )
    return _parse_opencli_json(out.stdout, out.returncode, site, limit)


def _fetch_opencli_userposts(site: str, user: str, limit: int) -> list[dict]:
    """跟踪指定 KOL/官号主页（user-posts 入口）。"""
    out = subprocess.run(
        [_OPENCLI, site, "user-posts", user, "-f", "json"],
        capture_output=True, encoding="utf-8", errors="replace", timeout=120)
    if out.returncode != 0:
        raise RuntimeError(f"opencli {site} user-posts 失败: {out.stderr[:200]}")
    data = json.loads(out.stdout or "[]")
    return (data if isinstance(data, list) else data.get("items") or data.get("data") or [])[:limit]


# --- 黑猫投诉：登录态浏览器桥（tousu.sina.com.cn 搜索页需微博登录 + 站内 JS 签名，
#     只能让已登录的真实浏览器自己渲染，再从 markdown 抓投诉详情链接）---

# 锚定"域名 + ≥9 位投诉 id"，对页面 DOM/路径变化鲁棒；id 即天然去重键。
_HEIMAO_LINK = re.compile(
    r"\[([^\]]{2,}?)\]\((https://tousu\.sina\.com\.cn/[^)]*?(\d{9,})[^)]*)\)")


def parse_heimao_markdown(md: str) -> list[dict]:
    """从 opencli browser extract 的 markdown 里抽投诉条目（按详情链接锚定，含去重）。

    返回与 normalize() 兼容的 item：{id, text, url}。纯函数，可离线测。
    """
    seen: set[str] = set()
    items: list[dict] = []
    for title, url, cid in _HEIMAO_LINK.findall(md or ""):
        if cid in seen:
            continue
        seen.add(cid)
        items.append({"id": cid, "text": title.strip(), "url": url})
    return items


def _opencli_browser(session: str, *args: str, timeout: int = 60) -> str:
    out = subprocess.run(
        [_OPENCLI, "browser", session, *args],
        capture_output=True, encoding="utf-8", errors="replace", timeout=timeout)
    if out.returncode != 0:
        raise RuntimeError(f"opencli browser {' '.join(args)[:40]} 失败: {(out.stderr or '')[:200]}")
    return out.stdout or ""


def _fetch_heimao(keyword: str, limit: int, *, pages: int = 1) -> list[dict]:
    """驱动登录态 Chrome 抓黑猫搜索结果。

    前置（一次性）：桌面 Chrome 登录微博后，`opencli browser <session> bind` 绑定标签页。
    session 名取自 env YUQING_OPENCLI_SESSION（默认 'yuqing'）。
    ponytail: 此路径依赖用户本机登录态，无法在 CI 离线验证；抓取失败会 raise →
    上层记 status=error → 健康三态判 fail（"没抓到"≠"没负面"）。真实选择器/等待时机
    是"校准旋钮"，首次在本机跑通时按需微调。
    """
    session = os.getenv("YUQING_OPENCLI_SESSION", "yuqing")
    items: list[dict] = []
    seen: set[str] = set()
    for page in range(1, pages + 1):
        # 注意：URL 里的 & 在 Windows 上会被 opencli.CMD 的 cmd.exe 当命令分隔符（'page' not found）。
        # 第 1 页不带 &page（tousu 默认第一页）；翻页(page>1)属 Phase 1+，届时需处理 & 转义。
        url = f"https://tousu.sina.com.cn/index/search/?keywords={quote(keyword)}"
        if page > 1:
            url += f"&page={page}"
        _opencli_browser(session, "open", url)
        md = ""
        for _ in range(3):                       # 列表可能异步渲染，重试几次再放弃
            time.sleep(1.5)
            md = _opencli_browser(session, "extract")
            if _HEIMAO_LINK.search(md):
                break
        for it in parse_heimao_markdown(md):
            if it["id"] not in seen:
                seen.add(it["id"])
                items.append(it)
        if len(items) >= limit:
            break
    return items[:limit]


def collect_platform(store: Store, *, run_id: str, entity_id: str, platform: str, keyword: str,
                     now: str, limit: int = 50, fixture: Optional[list[dict]] = None,
                     backend: str = "opencli", entry: str = "search",
                     user: Optional[str] = None,
                     aliases: Optional[list] = None, must_not: Optional[list] = None) -> tuple[int, str]:
    """采集一个 (实体,平台)。返回 (新入库条数, 健康三态)。fixture 非空则走离线。

    aliases/must_not 做串味过滤：命中否定词或(search入口下)一个别名都不含 → 判无关，
    留原始层审计但不进 clean。无 aliases 时不强求含别名（只挡否定词）。
    """
    try:
        if fixture is not None:
            items = fixture
        elif entry == "user-posts" and user:
            items = _fetch_opencli_userposts(OPENCLI_SITE[platform], user, limit)
        elif platform == "heimao":
            items = _fetch_heimao(keyword, limit)      # 登录态浏览器桥
        else:
            items = _fetch_opencli(platform, keyword, limit)
        status = "ok"
        note = ""
    except Exception as e:                       # 采集失败 ≠ 无负面，单列失败信号
        items, status, note = [], "error", str(e)[:200]

    # 增量水位：只用 ISO 日期串比较，跳过严格早于水位的内容（幂等去重仍由 UNIQUE 兜底，
    # 宁可重抓不可漏；非 ISO/数字时间戳一律不参与水位，避免污染导致静默漏抓）。
    # 只有 opencli 模糊搜索适配器(weibo/zhihu/…)才易串味需强制含别名；heimao(浏览器桥，
    # 搜索已按关键词定向、extract 仅标题) 与 user-posts(定向账号) 不强求，否则会漏掉真实投诉。
    require = (entry == "search") and bool(aliases) and (platform in OPENCLI_SITE)
    watermark = store.get_watermark(entity_id, platform, entry)
    max_ts = watermark or ""
    inserted = 0
    n_valid = 0                                  # 含有效 native_id、可解析的条数
    n_mustnot = 0                                # 命中否定词被过滤
    n_noalias = 0                                # 不含任何别名被过滤
    for it in items:
        doc = normalize(platform, entity_id, it, backend, now)
        if not doc.native_id:
            continue
        n_valid += 1
        store.add_raw(doc, it)                   # 全部留原始层审计（含被过滤的）
        v = relevance.judge(doc.text, aliases or [], must_not, require_alias=require)
        if not v.relevant:                       # 串味/无关：不进 clean
            if v.reason.startswith("must_not"):
                n_mustnot += 1
            else:
                n_noalias += 1
            continue
        ts = doc.publish_ts if _ISO_TS.match(doc.publish_ts) else ""
        if watermark and ts and ts < watermark:
            continue                             # 严格早于水位，跳过
        if ts > max_ts:
            max_ts = ts
        if store.add_clean(doc):                 # True=新插入（UNIQUE 去重）
            inserted += 1
    if max_ts and max_ts != watermark:
        store.set_watermark(entity_id, platform, entry, max_ts)

    state = health.assess(store, platform=platform, entity_id=entity_id,
                          n_fetched=len(items), status=status)
    # 抓到了但一条都解析不出 → 多半平台字段格式变了，绝不能顶着 ok 静默丢数据
    if status == "ok" and len(items) > 0 and n_valid == 0:
        state = "suspect"
        note = note or f"字段映射失败：抓到 {len(items)} 条但 0 条含有效ID（平台格式可能变了）"
    n_offtopic = n_mustnot + n_noalias
    if n_offtopic:
        note = (note + "；" if note else "") + f"过滤 must_not{n_mustnot}/无别名{n_noalias}(共{n_offtopic}/{n_valid})"
    store.log_run(run_id, platform, entity_id, len(items), status, state, note, now)
    store.commit()
    return inserted, state


def collect_all(store: Store, watch: dict, *, run_id: str, now: str,
                fixtures: Optional[dict] = None) -> dict[str, str]:
    """按 watch 配置采集所有实体×平台。返回 {platform: 健康态}（用于报告红条）。"""
    fixtures = fixtures or {}
    health_by_platform: dict[str, str] = {}
    for ent in watch["entities"]:
        eid = ent["id"]
        kw = ent.get("aliases", [ent["id"]])[0]
        aliases = ent.get("aliases", [])
        must_not = ent.get("must_not", [])
        for platform in watch["platforms"]:
            fx = (fixtures.get(platform) or {}).get(eid) if fixtures else None
            _, state = collect_platform(store, run_id=run_id, entity_id=eid, platform=platform,
                                        keyword=kw, now=now, fixture=fx,
                                        aliases=aliases, must_not=must_not)
            # 一个平台多实体时取最差态
            health_by_platform[platform] = health.worst(health_by_platform.get(platform), state)
        # user-posts 入口：跟踪指定 KOL/官号（track_users: ["weibo:12345", ...]）
        for spec in ent.get("track_users", []):
            site, _, uid = spec.partition(":")
            if site in OPENCLI_SITE and uid:
                collect_platform(store, run_id=run_id, entity_id=eid, platform=site,
                                 keyword=kw, now=now, entry="user-posts", user=uid,
                                 aliases=aliases, must_not=must_not)
    return health_by_platform


if __name__ == "__main__":
    d = normalize("weibo", "myproduct",
                  {"id": "123", "text": "申请退款一直不理，避雷这个牌子",
                   "user": {"nickname": "路人", "followers": "1.2万"}, "like_count": "3000"},
                  backend="opencli", fetched_at="2026-07-06T10:00:00+08:00")
    assert d.doc_id and d.is_complaint and d.author_followers == 12000 and d.likes == 3000

    # 黑猫 markdown 解析：按详情链接锚定 + 去重（模拟 extract 输出）
    sample = (
        "### 投诉列表\n"
        "* [星海手机屏幕碎裂要求退款商家拒绝](https://tousu.sina.com.cn/complaint/view/17359912345/)\n"
        "  投诉对象：星海科技 进度：处理中\n"
        "* [购买星海Pro七天无理由退货被拒](https://tousu.sina.com.cn/complaint/view/17359988888/)\n"
        "* [重复链接应被去重](https://tousu.sina.com.cn/complaint/view/17359912345/)\n"
        "* [无关导航](https://tousu.sina.com.cn/index/index/)\n"   # 无 id → 不计
    )
    parsed = parse_heimao_markdown(sample)
    assert len(parsed) == 2, parsed                       # 去重 + 过滤无 id
    assert parsed[0]["id"] == "17359912345" and "退款" in parsed[0]["text"]
    hm = normalize("heimao", "myproduct", parsed[1], backend="opencli-browser",
                   fetched_at="2026-07-06T10:00:00+08:00")
    assert hm.is_complaint and hm.native_id == "17359988888"   # 黑猫恒为投诉

    # opencli 空结果(NOT_FOUND) 当 []，真失败才 raise
    assert _parse_opencli_json('{"ok":false,"error":{"code":"NOT_FOUND"}}', 1, "weibo", 50) == []
    try:
        _parse_opencli_json('{"ok":false,"error":{"code":"NOT_LOGGED_IN","message":"登录"}}', 1, "weibo", 50)
        raise AssertionError("登录失败应 raise")
    except RuntimeError:
        pass
    assert len(_parse_opencli_json('[{"id":"1"},{"id":"2"}]', 0, "weibo", 1)) == 1   # limit 截断

    # 知乎无 id 字段：从 url 末段派生 native_id；votes 计为互动
    zh = normalize("zhihu", "e", {"rank": 1, "title": "评测", "author": "作者", "votes": 6,
                                  "url": "https://zhuanlan.zhihu.com/p/2055758079493510613"},
                   backend="opencli", fetched_at="2026-07-06T10:00:00+08:00")
    assert zh.native_id == "2055758079493510613" and zh.likes == 6, (zh.native_id, zh.likes)

    # 字段映射失败保护：抓到但全部无 id → 健康判 suspect（不静默顶 ok）
    from .store import Store as _S
    st = _S(":memory:")
    _n, _state = collect_platform(st, run_id="r", entity_id="e", platform="weibo", keyword="k",
                                  now="2026-07-06T10:00:00+08:00",
                                  fixture=[{"rank": 1, "title": "无id无url的脏数据"}])
    assert _state == "suspect", f"映射失败应判 suspect，实际 {_state}"
    print("OK collect: doc_id=", d.doc_id, "| 黑猫解析", len(parsed),
          "条 | 空/失败区分✓ | url派生id✓ | 映射失败保护✓")
