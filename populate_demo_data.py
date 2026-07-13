# -*- coding: utf-8 -*-
"""生成与当前 watch.yaml 对齐的独立工作台联调数据库。

默认写入 ``yuqing-demo.db``，绝不删除或覆盖现有文件。需要重建时必须显式传入
``--force``；生产数据库 ``yuqing.db`` 不应作为本脚本的目标。
"""

from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

from yuqing import load_watch
from yuqing.report import build_report
from yuqing.store import CleanDoc, Store


FIXTURES = [
    ("weibo", "wb-hot", "数码博主王小明", "{product} 玩游戏二十分钟就明显发热，客服一直没有解决。", 1520, "neg", 0.42, 92, False, False),
    ("weibo", "wb-battery", "消费者李华", "刚买的 {product} 续航太差，一天要充三次电。", 890, "neg", 0.58, 61, False, False),
    ("weibo", "wb-positive", "手机测评师", "{product} 屏幕显示效果不错，系统更新后也更流畅。", 560, "pos", 0.91, 3, False, False),
    ("xiaohongshu", "xhs-return", "小红薯用户A", "{product} 申请退货一周还没有处理，售后回复很慢。", 234, "neg", 0.73, 54, False, False),
    ("xiaohongshu", "xhs-irony", "小红薯用户B", "{product} 这旗舰散热真厉害，冬天都不用买暖手宝了。", 156, "neg", 0.77, 38, False, True),
    ("xiaohongshu", "xhs-design", "小红薯测评师", "{product} 外观设计很简洁，握持手感比预期好。", 445, "pos", 0.88, 2, False, False),
    ("zhihu", "zh-system", "知乎用户甲", "{product} 系统偶尔卡顿，部分应用会闪退，希望尽快优化。", 789, "neg", 0.55, 48, False, False),
    ("zhihu", "zh-review", "知乎数码达人", "同价位里 {product} 的游戏兼容性和性价比都不错。", 234, "pos", 0.86, 4, False, False),
    ("heimao", "hm-refund", "投诉用户001", "购买 {product} 后三天出现质量问题，商家拒绝退换货，要求退款。", 0, "neg", 0.82, 96, True, False),
    ("douyin", "dy-signal", "抖音用户吐槽", "{product} 在地铁里经常断网，信号表现需要改进。", 5600, "neg", 0.69, 44, False, False),
]


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成安全、可操作的工作台联调数据库")
    parser.add_argument("--db", default="yuqing-demo.db", help="输出数据库，默认 yuqing-demo.db")
    parser.add_argument("--force", action="store_true", help="显式允许覆盖指定的联调数据库")
    return parser.parse_args()


def _prepare_target(path: Path, *, force: bool) -> None:
    if path.name == "yuqing.db":
        raise SystemExit("拒绝写入默认生产数据库 yuqing.db；请使用独立的 --db 路径")
    if path.exists() and not force:
        raise SystemExit(f"{path} 已存在；如确认重建，请追加 --force")
    if force:
        for candidate in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
            try:
                candidate.unlink()
            except FileNotFoundError:
                pass
    path.parent.mkdir(parents=True, exist_ok=True)


def _aspect_signals(text: str) -> list[dict]:
    definitions = {
        "硬件质量": ("发热", "质量", "散热"),
        "系统体验": ("系统", "卡顿", "闪退", "信号"),
        "售后服务": ("客服", "售后", "退款", "退货"),
        "游戏兼容": ("游戏", "兼容"),
        "外观": ("外观", "手感", "屏幕"),
        "续航": ("续航", "充电"),
    }
    return [
        {"aspect": aspect, "polarity": "neg" if any(word in text for word in ("差", "慢", "拒绝", "卡顿", "发热", "断网")) else "pos"}
        for aspect, words in definitions.items() if any(word in text for word in words)
    ]


def build_demo_database(path: Path, *, force: bool = False) -> dict:
    _prepare_target(path, force=force)
    watch = load_watch()
    entity = next(
        (item for item in (watch.get("entities") or []) if item.get("type", "self") == "self"),
        None,
    )
    if entity is None:
        raise SystemExit("watch.yaml 未配置自有监控对象")
    entity_id = str(entity["id"])
    product = str((entity.get("aliases") or [entity_id])[0])
    now_dt = dt.datetime.now().astimezone().replace(microsecond=0)
    now = now_dt.isoformat()
    run_id = "demo-" + now_dt.strftime("%Y%m%d-%H%M%S")
    store = Store(path)
    doc_ids: list[str] = []
    platform_counts: dict[str, int] = {}
    try:
        for index, fixture in enumerate(FIXTURES):
            platform, native_id, author, template, likes, polarity, confidence, risk, complaint, ironic = fixture
            text = template.format(product=product)
            published = (now_dt - dt.timedelta(days=index % 5, hours=index)).isoformat()
            doc = CleanDoc.build(
                platform=platform,
                native_id=native_id,
                entity_id=entity_id,
                text=text,
                author=author,
                author_followers=max(likes * 8, 100),
                likes=likes,
                comments=max(likes // 20, 0),
                reposts=max(likes // 50, 0),
                publish_ts=published,
                fetched_at=now,
                url=f"https://example.com/{platform}/{native_id}",
                is_complaint=complaint,
                backend="demo",
            )
            store.add_clean(doc)
            signals = {"aspects": _aspect_signals(text)}
            if complaint:
                signals["crisis"] = True
            if native_id == "zh-system":
                signals["cross_disagree"] = True
            store.add_feature(doc.doc_id, {
                "polarity": polarity,
                "confidence": confidence,
                "risk": risk,
                "is_ironic": ironic,
                "signals": signals,
                "topic_label": signals["aspects"][0]["aspect"] if signals["aspects"] else "其他",
                "summary": text[:60],
                "evidence": text[:30],
            }, analyzed_at=now)
            doc_ids.append(doc.doc_id)
            platform_counts[platform] = platform_counts.get(platform, 0) + 1

        health_by_platform = {}
        for platform in [str(item) for item in (watch.get("platforms") or [])]:
            count = platform_counts.get(platform, 0)
            health_by_platform[platform] = "ok"
            store.log_run(
                run_id, platform, entity_id, count, "ok", "ok",
                "联调数据：该平台本轮无样本" if count == 0 else "联调数据已就绪",
                now, entry="demo", source_query=product,
            )

        store.create_incident(
            entity_id=entity_id,
            cluster_key="demo-refund-quality",
            level="P0",
            doc_id=doc_ids[8],
            summary=f"{product} 质量与退款投诉待确认",
            ts=now,
        )
        build_report(
            store, watch, run_id=run_id, now=now,
            health_by_platform=health_by_platform, use_claude=False,
        )
        store.commit()
        stats = store.conn.execute(
            "SELECT COUNT(*) total,SUM(CASE WHEN f.polarity='neg' THEN 1 ELSE 0 END) negative "
            "FROM clean c JOIN features f USING(doc_id)"
        ).fetchone()
        return {
            "db": str(path), "entity_id": entity_id, "product": product,
            "total": stats["total"], "negative": stats["negative"],
            "pending_reviews": store.pending_review_count(), "run_id": run_id,
        }
    finally:
        store.close()


def main() -> int:
    args = _arguments()
    result = build_demo_database(Path(args.db), force=args.force)
    print("联调数据库已生成：")
    print(f"  文件：{result['db']}")
    print(f"  监控对象：{result['product']} ({result['entity_id']})")
    print(f"  内容：{result['total']} 条；负面 {result['negative']} 条；待复核 {result['pending_reviews']} 条")
    print(f"  运行/报告：{result['run_id']}；待确认事件 1 条")
    print("启动示例：")
    print(f"  python3 -c \"from yuqing.dashboard import serve; serve(db='{result['db']}')\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
