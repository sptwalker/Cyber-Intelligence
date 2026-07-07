# -*- coding: utf-8 -*-
"""yuqing 命令行：问答 / 时间线 / 诉求清单 / 老板日报（读已有库，不采集）。

    python -m yuqing.cli ask "发热问题主要在哪些平台"
    python -m yuqing.cli timeline "星海手机"
    python -m yuqing.cli backlog [out.csv]
    python -m yuqing.cli daily
    python -m yuqing.cli review [stats | <doc_id> <结论> [备注]]   # 人工复核队列/标注
    python -m yuqing.cli suggest                                   # 语义扩展：建议加入监控的新词/话题
"""

from __future__ import annotations

import datetime as _dt
import sys

from . import load_watch
from .insights import ask, timeline, backlog, backlog_csv, oneliner
from .store import Store


def main(argv: list[str]) -> int:
    if len(argv) < 1:
        print(__doc__)
        return 1
    cmd, args = argv[0], argv[1:]
    store = Store("yuqing.db")
    try:
        if cmd == "ask" and args:
            r = ask(store, " ".join(args))
            print(r["answer"])
            print("来源:", ", ".join(r["sources"]) or "无")
        elif cmd == "timeline" and args:
            for t in timeline(store, " ".join(args)):
                print(f"{t['time']:20} {t['platform']:10} {t['summary']}  [{t['doc_id']}]")
        elif cmd == "backlog":
            items = backlog(store)
            csv = backlog_csv(items)
            if args:
                with open(args[0], "w", encoding="utf-8") as f:
                    f.write(csv)
                print(f"已导出 {len(items)} 条诉求 → {args[0]}")
            else:
                print(csv)
        elif cmd == "daily":
            print(oneliner(store, load_watch()))
        elif cmd == "review":
            if not args:                                   # 列出待复核队列
                q = store.review_queue()
                print(f"待复核 {store.pending_review_count()} 条（显示前 {len(q)}，按风险降序）：")
                for r in q:
                    flag = "🌀反讽" if r["is_ironic"] else ""
                    print(f"  {r['doc_id']} [{r['platform']}] {r['polarity']}"
                          f"(conf{r['confidence']:.2f} risk{r['risk']}){flag} {(r['text'] or '')[:40]}")
                print("标注: python -m yuqing.cli review <doc_id> <结论> [备注]"
                      "  结论如 ok/改负/改正/串味/水军/危机确认")
            elif args[0] == "stats":
                s = store.review_stats()
                print((f"已复核 {s['reviewed']} 条，机器判错 {s['machine_wrong']} 条"
                       f"（复核样本[最难的低置信/高风险]判错率 {s['machine_wrong'] / s['reviewed']:.0%}，"
                       f"非全量准确率）") if s["reviewed"] else "尚无复核记录")
            elif len(args) < 2:                            # 只给 doc_id 不给结论 → 拒绝，防误标"ok"
                print("需要提供结论，例：review <doc_id> 改负 [备注]"
                      "（结论 ok/改负/改正/串味/水军/危机确认）")
                return 1
            else:                                          # 记录复核结论
                doc_id, verdict = args[0], args[1]
                note = " ".join(args[2:])
                store.add_review(doc_id, verdict, note,
                                 ts=_dt.datetime.now().astimezone().isoformat(timespec="seconds"))
                print(f"已记录复核：{doc_id} → {verdict}" + (f"（{note}）" if note else ""))
        elif cmd == "suggest":                             # 语义扩展：建议加入监控的新词/话题
            from . import analytics
            watch = load_watch()
            any_out = False
            for ent in watch.get("entities", []):
                if ent.get("type", "self") != "self":
                    continue
                aliases = ent.get("aliases") or [ent["id"]]
                sug = analytics.suggest_targets(store, ent["id"], aliases, ent.get("must_not"))
                if not sug:
                    continue
                any_out = True
                print(f"【{aliases[0]}】建议加入监控（语义相关但当前词汇未覆盖，人工确认后写 watch.yaml）：")
                for x in sug:
                    print(f"  相似{x['avg_sim']} ×{x['size']} [{'、'.join(x['platforms'])}] {x['sample']}")
            if not any_out:
                print("无建议（需配置 EMBED_API_KEY 且有已向量化的相关数据）。")
        else:
            print(__doc__)
            return 1
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
