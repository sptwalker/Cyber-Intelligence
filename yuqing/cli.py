# -*- coding: utf-8 -*-
"""yuqing 命令行：问答 / 时间线 / 诉求清单 / 老板日报（读已有库，不采集）。

    python -m yuqing.cli ask "发热问题主要在哪些平台"
    python -m yuqing.cli timeline "星海手机"
    python -m yuqing.cli backlog [out.csv]
    python -m yuqing.cli daily
"""

from __future__ import annotations

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
        else:
            print(__doc__)
            return 1
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
