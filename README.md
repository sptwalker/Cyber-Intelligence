# Cyber-Intelligence · yuqing 舆情监控与分析系统

> 给团队一双看清全网口碑的眼睛：自动采集 → 情绪/语义分析 → 风险识别 → 周期报告 + 实时预警，结论**可溯源到原帖**。

基于 [Agent-Reach](https://github.com/Panniantong/Agent-Reach) 的"能力层"理念构建——**复用已登录的浏览器会话采集，零 API 费**；不自己造爬虫。面向出海硬件品牌（Youdoo Box）自有口碑 + 竞品（KINHANK）监控。

**已接入 7 平台**（真实数据验证）：微博 · 知乎 · 小红书 · 抖音 · B站 · 贴吧（免登录）· 黑猫投诉。快手 opencli 无适配器，暂不支持。

## 现状

- **技术底座已建成并用真实数据跑通**（Phase 0–3）：微博/知乎真实采集（115 条/期），出带 SOV/ABSA/负面Top/诉求 backlog 的可溯源周报。
- **本质仍是"单机工具"**：单人登录态 Chrome + 手动触发。从"工具"到"面向全公司的产品"的完整设计见 👉 [`docs/产品设计方案.md`](docs/产品设计方案.md)。
- 一句话判断：**算法能力已过剩，真实差距是三块地基——数据质量 / 常驻可靠性 / 组织责任**，均与新功能无关。

## 快速开始

面向舆情分析师的完整页面和日常值班说明见 [`docs/用户操作指南.md`](docs/用户操作指南.md)。

前置：Python 3.10+，推荐 `pip install -e .`；Claude/语义能力可用
`pip install -e '.[all]'` 安装可选依赖。采集需本机
[opencli](https://github.com/jackwener/opencli) + 对应平台登录态。未安装可选 SDK 或 API 不可用时自动降级，不阻塞规则分析和确定性报告。

```bash
python -m yuqing.selfcheck        # 端到端离线自检（无需登录/API key），exit 0 = 全链通
python -m yuqing.architecture_check # 核心架构回归：多实体/快照/版本/告警确认门
python -m yuqing.run              # 跑一次完整流水线：采集→分析→预警→周报→飞书
python -m yuqing.dashboard        # 看板 → 浏览器开 http://127.0.0.1:8000
                                  #   / 详情看板 · /exec 高管概览(BHI) · /dash 中层战情室(Chart.js) · /config 配置
python -m yuqing.cli daily        # 老板一句话日报
python -m yuqing.cli ask "发热问题在哪些平台"   # AI 舆情问答（语义检索，无 key 回退词汇）
python -m yuqing.cli review        # 人工复核队列（数据质量）
python -m yuqing.cli suggest       # 语义扩展：建议加入监控的新词/话题（需 EMBED_API_KEY）
```

如需在不连接真实平台、也不覆盖现有 `yuqing.db` 的情况下体验可操作工作台，可生成独立联调库：

```bash
python3 populate_demo_data.py
python3 -c "from yuqing.dashboard import serve; serve(db='yuqing-demo.db')"
```

联调库包含与当前 `watch.yaml` 对齐的内容、待复核队列、平台运行记录、待确认事件和历史报告；重建时需显式追加 `--force`。

三层视图（一份数据，三张皮肤）：**高管** `/exec` 品牌健康指数 BHI + 危机灯 + 关键结论；**中层** `/dash` Chart.js 战情室（情绪/声量/BHI趋势 + 方面雷达 + KOL榜 + 异常账号簇 + 竞品SOV）；**基层** `/` 详情看板 + 复核队列 + CLI。

**语义向量化**（配置页填 `EMBED_API_KEY`=阿里百炼即启用，无 key 全部降级回词汇匹配、不阻塞）：语义检索（问"电池"召回只说"续航"的帖）· 话题语义归并（"续航差"+"电池不耐用"归一簇）· 洗稿近似去重（改写控评识别为同簇）· 监控目标语义扩展（`cli suggest` 从数据发现该监控的新词，人工确认）。可选语义相关性过滤 `SEMANTIC_RELEVANCE=1`（召回不含品牌字面的相关帖，默认关）。

监控对象配置在 [`yuqing/watch.yaml`](yuqing/watch.yaml)（实体/别名/否定词/危机词，git 版本化）。

## 架构

```
watch.yaml → collect(opencli登录态,混合,全部aliases) → SQLite(raw_observations/clean/document_entities)
           → analyze(规则/Claude,ABSA,证据校验) → score(线性加权风险)
           → analysis_results(版本血缘) → report(数字注入+引用校验) / incidents(P0/P1待确认→升级)
           → 飞书 + 只读看板 ；贯穿：健康三态+静默失败熔断
```

| 模块 | 职责 |
|---|---|
| `collect.py` | 采集+归一化，登录态桥/字段映射/增量水位/健康三态 |
| `store.py` | SQLite 分层 + 统一 doc_id 契约 + 幂等去重 |
| `analyze.py` | 情绪/ABSA/信息抽取（规则 stub + Claude tool use），evidence 逐字校验 |
| `score.py` | 线性加权风险分（平台×情绪×危机×影响力，可解释） |
| `alerts.py` | P0/P1 实时预警，事件簇冷却，竞品不误告警 |
| `analytics.py` | 稳健 z-score 异常 / ABSA 聚合 / 上升话题 |
| `report.py` | 周报生成（数字注入 + 引用校验器）+ 飞书推送 + SOV |
| `insights.py` | 老板日报 / AI 问答 / 诉求→需求 / 事件时间线 |
| `dashboard.py` | stdlib 只读看板（健康/趋势/负面Top/报告） |
| `health.py` `budget.py` | 数据健康三态 / 成本配额熔断 |
| `selfcheck.py` | 端到端离线自检（是改代码后的验收基准） |

## 路线图（详见设计文档第十章）

| 阶段 | 目标 | 状态 |
|---|---|---|
| 0 现状 | 真实数据跑通 | ✅ 已完成 |
| 1 内部可用 MVP | 可信数据地基 + 无人值守（串味过滤/常驻调度/复核队列/可信度标记） | 🚧 开发中 |
| 2 部门推广 | 多用户分角色（Postgres/RBAC/部门看板） | 规划 |
| 3 全公司铺开 | 高层可靠 + P0 危机 SLA | 规划 |

## 诚实的限制

- **数据质量是命门**：关键词易串味（搜 "Youdoo Box" 会串出 "Doo Prime" 外汇/创维新闻）→ 靠 `must_not` + 人工复核收紧（v1 已落地）。
- **微博搜索无互动数**（点赞/转发/粉丝）→ 影响力加权退化，报告标 ⚠降级；**抖音搜索带真实互动**（plays/likes/comments/shares）不降级。
- **小红书/抖音/黑猫需登录态**：opencli Chrome 各登录一次（`opencli xiaohongshu login`、`opencli douyin login`；tousu.sina.com.cn 手动登录）。出海品牌在这些平台常为 0 结果=正常空（不判 fail）。
- **单用户**：SQLite 单写者，多用户需迁 Postgres（阶段 2）。
- **报告链接是 127.0.0.1**：仅本机浏览器可看；手机/多人需部署到可访问地址（配置页 DASHBOARD_URL）。
- 情绪判定含中文反讽误判风险，关键负面结论建议人工抽检。

## 许可 / 合规

内部工具。采集仅限公开数据、限速错峰、只用可牺牲的专用监控号；对外交付默认脱敏聚合，遵循 PIPL 与平台 ToS（合规待法务背书）。
