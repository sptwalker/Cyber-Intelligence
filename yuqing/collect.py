# -*- coding: utf-8 -*-
"""采集层：复用 Agent-Reach / opencli，不写爬虫。

- 在线：subprocess 调 `opencli <site> search "<kw>" -f json`，输出直接归一化。
- 离线：读 fixtures（canned json），让整条链在无登录态/无网络时可跑可测。
职责只到 clean 层（含 is_complaint 词典派生），情绪/topic 留给 analyze。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from urllib.parse import quote
from typing import Optional

from .normalization import normalize
from .store import Store
from . import health
from . import relevance

# Windows 上 opencli 是 .CMD 脚本无 .exe，subprocess 裸名 CreateProcess 找不到（不套 PATHEXT）。
# 用 shutil.which 解析全路径；mac/Linux 返回普通路径，同样正确。
_OPENCLI = shutil.which("opencli") or "opencli"

# 平台名 → opencli site。黑猫(heimao)无 opencli 后端，走 browser 通用桥（见 _fetch_heimao）。
OPENCLI_SITE = {"weibo": "weibo", "zhihu": "zhihu", "douyin": "douyin",
                "xiaohongshu": "xiaohongshu", "bilibili": "bilibili", "tieba": "tieba",
                "weixin": "weixin", "hupu": "hupu", "smzdm": "smzdm"}   # 视频号只能发布,不接

_ISO_TS = re.compile(r"^\d{4}-\d{2}-\d{2}")   # 仅 ISO 日期串可参与水位比较

_SEM_THRESHOLD = 0.55        # 语义相关性默认阈值（保守，宁缺毋滥防串味），可 config 覆盖


def _semantic_setup(require: bool, aliases):
    """语义相关性开关：仅 SEMANTIC_RELEVANCE=1 + 有 embedding key + search 入口 时启用。

    返回 (是否启用, 阈值, 监控对象参考向量)。参考向量=别名短语的 embedding（算一次）。
    任何不满足/出错 → 关闭(降级到纯词汇)，绝不阻塞采集。
    """
    from . import config, embed
    if not (require and aliases and config.resolve("SEMANTIC_RELEVANCE") in ("1", "true", "True")):
        return False, 0.0, None
    if not embed.available():
        return False, 0.0, None
    try:
        thr = float(config.resolve("SEMANTIC_THRESHOLD") or _SEM_THRESHOLD)
        vec = embed.embed_one("、".join(aliases[:3]))   # 别名短语作监控对象语义锚
        return (bool(vec), thr, vec)
    except Exception:
        return False, 0.0, None


def _semantic_sim(sem_on: bool, ent_vec, text: str):
    """算一条帖子与监控对象的语义相似度；关闭/出错→None（judge 走纯词汇）。"""
    if not (sem_on and ent_vec and text):
        return None
    from . import embed
    try:
        v = embed.embed_one(text)
        return embed.cosine(ent_vec, v) if v else None
    except Exception:
        return None


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
    from . import collector_client
    if collector_client.enabled():
        return collector_client.fetch(platform, keyword, limit)
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
    from . import collector_client
    if collector_client.enabled():
        return collector_client.fetch(site, "", limit, entry="user-posts", user=user)
    out = subprocess.run(
        [_OPENCLI, site, "user-posts", user, "-f", "json"],
        capture_output=True, encoding="utf-8", errors="replace", timeout=120)
    if out.returncode != 0:
        raise RuntimeError(f"opencli {site} user-posts 失败: {out.stderr[:200]}")
    data = json.loads(out.stdout or "[]")
    return (data if isinstance(data, list) else data.get("items") or data.get("data") or [])[:limit]


# --- 黑猫投诉：登录态浏览器桥（tousu.sina.com.cn 搜索页需微博登录 + 站内 JS 签名，
#     只能让已登录的真实浏览器自己渲染，再从 markdown 抓投诉详情链接）---

# 锚定投诉详情链接里的 ≥9 位 id。真实 markdown 里 URL 是协议相对(//tousu...)且带 query，
# 链接文字多行含转义括号——故只锚 URL+id，文字取链接前一段清洗后的内容。
_HEIMAO_LINK = re.compile(r"(?:https?:)?//tousu\.sina\.com\.cn/complaint/view/(\d{6,})")


def parse_heimao_markdown(md: str) -> list[dict]:
    """从 opencli browser extract 的 markdown 里抽投诉条目（按详情链接 id 锚定，含去重）。

    返回与 normalize() 兼容的 item：{id, text, url}。纯函数，可离线测。
    """
    md = md or ""
    seen: set[str] = set()
    items: list[dict] = []
    prev_end = 0
    for m in _HEIMAO_LINK.finditer(md):
        cid = m.group(1)
        seg = md[prev_end:m.start()]              # 该条投诉文字（到本链接锚为止）
        prev_end = m.end()
        if cid in seen:
            continue
        seen.add(cid)
        text = seg.replace("\\n", " ").replace("\\", "")     # 去转义(\n/\[)再清 markdown 噪声
        text = re.sub(r"[\n\r]+", " ", text)
        text = re.sub(r"[\[\]()*#>-]", " ", text)
        text = " ".join(text.split())[-140:].strip()
        items.append({"id": cid, "text": text or f"投诉{cid}",
                      "url": f"https://tousu.sina.com.cn/complaint/view/{cid}/"})
    return items


def _opencli_browser(session: str, *args: str, timeout: int = 60) -> str:
    out = subprocess.run(
        [_OPENCLI, "browser", session, *args],
        capture_output=True, encoding="utf-8", errors="replace", timeout=timeout)
    if out.returncode != 0:
        raise RuntimeError(f"opencli browser {' '.join(args)[:40]} 失败: {(out.stderr or '')[:200]}")
    return out.stdout or ""


def _heimao_is_login_wall(md: str) -> bool:
    """extract 无投诉链接时，判断是"登录墙(真失败)"还是"已登录但该词无投诉(正常空)"。

    可靠信号：已登录页含"退出"(logout)链接；未登录则无。登录模态文字(请直接登录/换个账号登录)
    在页面 DOM 里恒存在(隐藏)，不可用作判据。
    """
    return "退出" not in (md or "")


def _fetch_heimao(keyword: str, limit: int, *, pages: int = 1) -> list[dict]:
    """驱动登录态 Chrome 抓黑猫搜索结果。

    前置（一次性）：桌面 Chrome 登录微博后，`opencli browser <session> bind` 绑定标签页。
    session 名取自 env YUQING_OPENCLI_SESSION（默认 'yuqing'）。
    ponytail: 此路径依赖用户本机登录态，无法在 CI 离线验证；抓取失败会 raise →
    上层记 status=error → 健康三态判 fail（"没抓到"≠"没负面"）。真实选择器/等待时机
    是"校准旋钮"，首次在本机跑通时按需微调。
    """
    from . import collector_client
    if collector_client.enabled():
        return collector_client.fetch("heimao", keyword, limit)
    session = os.getenv("YUQING_OPENCLI_SESSION", "yuqing")
    items: list[dict] = []
    seen: set[str] = set()
    md = ""
    for page in range(1, pages + 1):
        # 注意：URL 里的 & 在 Windows 上会被 opencli.CMD 的 cmd.exe 当命令分隔符（'page' not found）。
        # 第 1 页不带 &page（tousu 默认第一页）；翻页(page>1)属 Phase 1+，届时需处理 & 转义。
        url = f"https://tousu.sina.com.cn/index/search/?keywords={quote(keyword)}"
        if page > 1:
            url += f"&page={page}"
        _opencli_browser(session, "open", url)
        md = ""
        for _ in range(4):                       # 等页面就绪：有投诉链接 或 登录头("退出")已渲染
            time.sleep(1.5)                       # 否则空结果页可能在头部渲染前被抓，误判登录墙
            md = _opencli_browser(session, "extract")
            if _HEIMAO_LINK.search(md) or "退出" in md:
                break
        for it in parse_heimao_markdown(md):
            if it["id"] not in seen:
                seen.add(it["id"])
                items.append(it)
        if len(items) >= limit:
            break
    # 无投诉时区分：登录墙(cookie失效)→raise→健康fail；已登录但该词无投诉→正常空(出海品牌黑猫常为0)
    if not items and _heimao_is_login_wall(md):
        raise RuntimeError("黑猫登录态失效（tousu.sina.com.cn 出现登录墙），请重新登录")
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
    require = entry.startswith("search") and bool(aliases) and (platform in OPENCLI_SITE)
    watermark = store.get_watermark(entity_id, platform, entry)
    max_ts = watermark or ""
    inserted = 0
    n_valid = 0                                  # 含有效 native_id、可解析的条数
    n_mustnot = 0                                # 命中否定词被过滤
    n_noalias = 0                                # 不含任何别名被过滤
    n_semantic = 0                               # 语义救回(不含别名但语义相似)
    # 语义相关性(V2-B,默认关)：SEMANTIC_RELEVANCE=1 且有 embedding key 时，
    # 对"不含别名"的候选算与监控对象的语义相似度，≥阈值则救回。双刃剑，宁缺毋滥。
    sem_on, sem_thr, ent_vec = _semantic_setup(require, aliases)
    for it in items:
        doc = normalize(platform, entity_id, it, backend, now)
        if not doc.native_id:
            continue
        n_valid += 1
        store.add_raw(doc, it, run_id=run_id, entry=entry, source_query=keyword)
        # 全部留原始观测审计（含被过滤的）；legacy raw 仍保留首见版本兼容旧查询。
        v = relevance.judge(doc.text, aliases or [], must_not, require_alias=require)
        # 仅当"无别名"被拒 且 语义开启时，才对这一条算 embedding 语义救回（省钱：不对已命中/已过审计的算）
        if not v.relevant and v.reason == "no_alias" and sem_on:
            sim = _semantic_sim(sem_on, ent_vec, doc.text)
            v = relevance.judge(doc.text, aliases or [], must_not, require_alias=require,
                                sem_sim=sim, sem_threshold=sem_thr)
        if not v.relevant:                       # 串味/无关：不进 clean
            if v.reason.startswith("must_not"):
                n_mustnot += 1
            else:
                n_noalias += 1
            continue
        if v.reason.startswith("semantic"):
            n_semantic += 1
        ts = doc.publish_ts if _ISO_TS.match(doc.publish_ts) else ""
        if watermark and ts and ts < watermark and not store.document_exists(doc.doc_id):
            continue                             # 旧且从未见过的结果跳过；已存在帖仍刷新互动/实体关系
        if ts > max_ts:
            max_ts = ts
        is_new = store.add_clean(doc)            # 已存在时刷新互动当前值并保存快照
        store.add_entity_match(doc.doc_id, entity_id, match_reason=v.reason,
                               source_query=keyword, observed_at=now)
        if is_new:
            inserted += 1
    if max_ts and max_ts != watermark:
        store.set_watermark(entity_id, platform, entry, max_ts)

    state = health.assess(store, platform=platform, entity_id=entity_id,
                          n_fetched=len(items), status=status,
                          entry=entry, source_query=keyword)
    # 抓到了但一条都解析不出 → 多半平台字段格式变了，绝不能顶着 ok 静默丢数据
    if status == "ok" and len(items) > 0 and n_valid == 0:
        state = "suspect"
        note = note or f"字段映射失败：抓到 {len(items)} 条但 0 条含有效ID（平台格式可能变了）"
    n_offtopic = n_mustnot + n_noalias
    if n_offtopic:
        note = (note + "；" if note else "") + f"过滤 must_not{n_mustnot}/无别名{n_noalias}(共{n_offtopic}/{n_valid})"
    if n_semantic:
        note = (note + "；" if note else "") + f"语义救回{n_semantic}(不含别名但语义相关)"
    store.log_run(run_id, platform, entity_id, len(items), status, state, note, now,
                  entry=entry, source_query=keyword)
    store.commit()
    return inserted, state


def collect_all(store: Store, watch: dict, *, run_id: str, now: str,
                fixtures: Optional[dict] = None,
                on_progress=None, should_stop=None) -> dict[str, str]:
    """按 watch 配置采集所有实体×平台。返回 {platform: 健康态}（用于报告红条）。

    on_progress(entity_id, platform): 每平台开采前回调（供 UI 显示"正在采集X…"）。
    should_stop() -> bool: 每平台前检查，返回 True 则协作式中止（已采数据保留）。
    """
    fixtures = fixtures or {}
    health_by_platform: dict[str, str] = {}
    for ent in watch["entities"]:
        eid = ent["id"]
        aliases, _seen_aliases = [], set()
        for alias in (ent.get("aliases") or [ent["id"]]):
            key = (alias or "").strip().casefold()
            if key and key not in _seen_aliases:
                _seen_aliases.add(key)
                aliases.append(alias.strip())
        if not aliases:
            aliases = [ent["id"]]
        kw = aliases[0]
        must_not = ent.get("must_not", [])
        for platform in watch["platforms"]:
            if should_stop and should_stop():
                return health_by_platform          # 协作式中止：停在平台边界，已采的保留
            fx = (fixtures.get(platform) or {}).get(eid) if fixtures else None
            # 线上每个 alias 都是真实搜索种子；fixture 代表平台聚合样本，只跑一次避免自检触网/重复。
            queries = [kw] if fx is not None else aliases
            for query in queries:
                if should_stop and should_stop():
                    return health_by_platform
                if on_progress:
                    on_progress(eid, platform)
                query_fixture = fx.get(query) if isinstance(fx, dict) else fx
                _, state = collect_platform(
                    store, run_id=run_id, entity_id=eid, platform=platform,
                    keyword=query, now=now, fixture=query_fixture,
                    entry=f"search:{query}", aliases=aliases, must_not=must_not)
                # 一个平台多实体/多搜索词时取最差态
                health_by_platform[platform] = health.worst(health_by_platform.get(platform), state)
        if should_stop and should_stop():
            return health_by_platform
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

    # 黑猫 markdown 解析：真实格式=协议相对URL + 多行含转义括号的文字 + 去重
    sample = (
        "投诉列表\n"
        "-   \\[投诉对象\\]星海科技\n-   \\[投诉要求\\]屏幕碎裂要求退款\n\n"
        "](//tousu.sina.com.cn/complaint/view/17359912345/?sld=abc)\n"
        "-   \\[投诉要求\\]七天无理由退货被拒\n\n](//tousu.sina.com.cn/complaint/view/17359988888/?sld=x)\n"
        "重复\n\n](//tousu.sina.com.cn/complaint/view/17359912345/)\n"        # 去重
        "导航](//tousu.sina.com.cn/index/index/)\n"                          # 无 view id → 不计
    )
    parsed = parse_heimao_markdown(sample)
    assert len(parsed) == 2, parsed                       # 去重 + 过滤非投诉链接
    assert parsed[0]["id"] == "17359912345" and "退款" in parsed[0]["text"]
    assert parsed[0]["url"] == "https://tousu.sina.com.cn/complaint/view/17359912345/"
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

    # 字段映射失败保护：抓到但全部无可用字段(无id/url/标题/正文) → 健康判 suspect（不静默顶 ok）
    from .store import Store as _S
    st = _S(":memory:")
    _n, _state = collect_platform(st, run_id="r", entity_id="e", platform="weibo", keyword="k",
                                  now="2026-07-06T10:00:00+08:00",
                                  fixture=[{"rank": 1, "unknownfield": "格式全变了"}])
    assert _state == "suspect", f"映射失败应判 suspect，实际 {_state}"

    # 黑猫登录墙检测(靠"退出"链接) + 空结果健康：未登录→wall；已登录无投诉→非wall(正常空→ok)
    assert _heimao_is_login_wall("新浪微博、博客、邮箱帐号，请直接登录\n[登录](javascript:;)")   # 无"退出"
    assert not _heimao_is_login_wall("暂无相关投诉\n-   [退出](javascript:;)")               # 有"退出"=已登录
    hs = _S(":memory:")
    _n2, _st2 = collect_platform(hs, run_id="r", entity_id="e", platform="heimao", keyword="出海冷门词",
                                 now="2026-07-06T10:00:00+08:00", fixture=[])   # 空但非登录墙
    assert _st2 == "ok", f"黑猫已登录但无投诉应 ok(非fail)，实际 {_st2}"

    # V2-B 语义相关性：默认关=行为不变；开(mock embed)=救回不含别名的相关帖
    assert _semantic_setup(True, ["Youdoo"])[0] is False       # 无 SEMANTIC_RELEVANCE=默认关
    import os as _os2
    from . import config as _cfg, embed as _emb2
    _os2.environ["SEMANTIC_RELEVANCE"] = "1"; _os2.environ["EMBED_API_KEY"] = "x"
    _emb2.embed_one = lambda t, **kw: ([1.0, 0.0] if "别名" in t or "Youdoo" in t else
                                       [0.95, 0.1] if "盒子" in t else [0.0, 1.0])  # 盒子帖语义近
    sem_store = _S(":memory:")
    collect_platform(sem_store, run_id="r", entity_id="e", platform="weibo", keyword="Youdoo",
                     now="2026-07-06T10:00:00+08:00", aliases=["Youdoo", "别名锚"], must_not=[],
                     fixture=[{"id": "a", "text": "这盒子巨卡发热"},          # 不含别名但语义近→救回
                              {"id": "b", "text": "今天天气真好"}])            # 无关→过滤
    kept = [r[0] for r in sem_store.conn.execute("SELECT native_id FROM clean")]
    assert kept == ["a"], f"语义应救回盒子帖a、滤掉无关b，实际 {kept}"
    _os2.environ.pop("SEMANTIC_RELEVANCE"); _os2.environ.pop("EMBED_API_KEY")
    print("OK collect: doc_id=", d.doc_id, "| 黑猫解析", len(parsed),
          "条 | 空/失败区分✓ | url派生✓ | 映射保护✓ | 登录墙vs真空✓ | 语义相关性(默认关/开救回)✓")
