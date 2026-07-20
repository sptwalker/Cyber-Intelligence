# -*- coding: utf-8 -*-
"""Embedding 接入层：文本向量化（阿里百炼 DashScope，OpenAI 兼容 /embeddings）。

复用 llm.py 的 provider+urllib+config 模式，stdlib 不加依赖。base/model 走 config 可覆盖
（端点/模型名是"校准旋钮"，首次带 key 用 `python -m yuqing.embed ping` 实测，不对就配置页改）。
向量存 SQLite BLOB、内存算余弦——数据量小（百/千条），零专用向量库。

无 key → available()=False，embed_texts 返回 None → 全部下游降级回词汇匹配，绝不阻塞跑批。
"""

from __future__ import annotations

import array
import json
import math
import urllib.request

from . import config

# OpenAI 兼容 /embeddings。百炼默认端点+模型（可 config 覆盖，text-embedding-v4 是当前版本）。
_PROVIDER = {
    "key": "EMBED_API_KEY", "base": "EMBED_BASE_URL",
    "base_def": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "model": "EMBED_MODEL", "model_def": "text-embedding-v4",
}


def available() -> bool:
    return bool(config.resolve(_PROVIDER["key"]))


def embed_texts(texts: list[str], *, timeout: int = 60) -> list[list[float]] | None:
    """批量向量化。无 key 返回 None（下游降级）；出错抛异常（上层 try 兜底降级）。"""
    key = config.resolve(_PROVIDER["key"])
    if not key or not texts:
        return None
    url = (config.resolve(_PROVIDER["base"]) or _PROVIDER["base_def"]).rstrip("/") + "/embeddings"
    model = config.resolve(_PROVIDER["model"]) or _PROVIDER["model_def"]
    body = json.dumps({"model": model, "input": texts}).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Authorization": f"Bearer {key}",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        resp = json.loads(r.read().decode("utf-8"))
    # OpenAI 兼容：{data:[{embedding:[...], index:n}]}，按 index 排序保证与输入对齐
    items = sorted(resp["data"], key=lambda d: d.get("index", 0))
    return [it["embedding"] for it in items]


def embed_one(text: str, **kw) -> list[float] | None:
    r = embed_texts([text], **kw)
    return r[0] if r else None


# --- 向量序列化（存 SQLite BLOB）+ 相似度（内存算）---
def to_blob(vec: list[float]) -> bytes:
    return array.array("f", [float(x) for x in (vec or [])]).tobytes()   # float() 挡 null/字符串元素


def from_blob(blob: bytes) -> list[float]:
    a = array.array("f")
    a.frombytes(blob)
    return list(a)


def cosine(a: list[float], b: list[float]) -> float:
    """余弦相似度 [-1,1]。零向量或维度不匹配 → 0.0（安全）。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def top_k_similar(query_vec: list[float], candidates: list[tuple], k: int = 8,
                  min_sim: float = 0.0) -> list[tuple]:
    """candidates: [(id, vec), ...] → [(id, sim), ...] 按相似度降序，过滤 < min_sim。"""
    scored = [(cid, cosine(query_vec, v)) for cid, v in candidates if v]
    scored = [x for x in scored if x[1] >= min_sim]
    return sorted(scored, key=lambda x: x[1], reverse=True)[:k]


def cluster(items: list[tuple], threshold: float = 0.75) -> list[list]:
    """向量单链聚类（贪心，百/千条级够用）：相似度 ≥ threshold 的归一簇。

    items: [(id, vec), ...] → [[id,...], [id,...]]（每簇一个 id 列表）。
    O(n²) 两两比较——数据量大再换。用于话题归并/洗稿去重。
    """
    reps: list[tuple] = []           # [(代表向量, [成员id])]
    for cid, vec in items:
        if not vec:
            reps.append((vec, [cid]))
            continue
        best_i, best_sim = -1, threshold
        for i, (rvec, _) in enumerate(reps):
            s = cosine(vec, rvec)
            if s >= best_sim:
                best_i, best_sim = i, s
        if best_i >= 0:
            reps[best_i][1].append(cid)
        else:
            reps.append((vec, [cid]))
    return [members for _, members in reps]


def probe() -> tuple[bool, str]:
    """连通测试（供设置页/CLI）。返回 (是否通, 说明)。"""
    if not available():
        return False, "未配置 EMBED_API_KEY"
    try:
        v = embed_one("测试文本")
        if v and len(v) > 0:
            return True, f"连通 ✓（维度 {len(v)}）"
        return False, "返回空向量"
    except Exception as e:
        return False, f"失败：{str(e)[:200]}"


def ensure_embeddings(store, *, now: str | None = None, batch: int = 10) -> int:
    """给缺向量的 clean 帖批量算 embedding 落库（缓存：只算缺的）。返回新算条数。

    无 key → 跳过返回 0（下游降级）。并入 budget.guard 计量。任何失败(API/解析/写库)都不抛，
    保证不阻塞跑批。batch 默认 10（DashScope 部分 embedding 模型每请求上限 10，可 EMBED_BATCH 覆盖）。
    """
    if not available():
        return 0
    rows = store.clean_missing_embedding()
    if not rows:
        return 0
    import datetime as _dt
    import sys as _sys
    from .budget import guard, BudgetExceeded
    day = (now or _dt.datetime.now().astimezone().isoformat())[:10]
    bsize = int(config.resolve("EMBED_BATCH") or batch)
    done = 0
    for i in range(0, len(rows), bsize):
        chunk = rows[i:i + bsize]
        texts = [r["text"] for r in chunk]
        try:                              # 每批一次 API 调用+落库整体兜底，任何失败降级停止(已算保留)
            guard(store, day, add_calls=1, add_tokens=sum(len(t) for t in texts))
            vecs = embed_texts(texts)
            if not vecs or len(vecs) != len(chunk):    # 数量不齐=响应残缺，整批弃(防错位存错向量)
                break
            for r, v in zip(chunk, vecs):
                store.set_embedding(r["doc_id"], to_blob(v))
                done += 1
            store.commit()
        except BudgetExceeded:
            break                         # 超限停止，已算的保留
        except Exception as e:
            print(f"[embed 批量失败，降级] {str(e)[:150]}", file=_sys.stderr)
            break
    return done


def main(argv: list[str] | None = None) -> None:
    """Run the supported connectivity probe; regression checks live in tests."""
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["ping"]:
        print(f"available={available()}")
        ok, msg = probe()
        print(msg)
    else:
        print("Usage: python -m yuqing.embed ping")


if __name__ == "__main__":
    main()
