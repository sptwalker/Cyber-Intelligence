# Cyber-Intelligence · yuqing 舆情监控与分析系统

> 给团队一双看清全网口碑的眼睛：自动采集 → 情绪/语义分析 → 风险识别 → 周期报告 + 实时预警，结论**可溯源到原帖**。

基于 [Agent-Reach](https://github.com/Panniantong/Agent-Reach) 的"能力层"理念构建——**复用已登录的浏览器会话采集，零 API 费**；不自己造爬虫。面向出海硬件品牌（Youdoo Box）自有口碑 + 竞品（KINHANK）监控。

**采集代码覆盖 10 个平台**：微博 · 知乎 · 小红书 · 抖音 · B 站 · 贴吧 · 虎扑 · 值得买 · 公众号走 opencli/Collector，黑猫投诉走登录态浏览器桥；快手没有适配器。平台适配器存在不代表当前机器已经具备采集条件，真实抓取仍需要 opencli/Collector 和对应登录态。

## 现状

- **一期工作台代码已接通**：总览、采集、质检、分析、预警、诉求、报告、监控配置八个页面均使用真实 `/api/v1/*` 接口；规则分析、确定性报告和 SQLite 持久化可离线运行。
- **真实采集是条件能力**：需要安装 opencli 或部署 Collector sidecar，并维护平台登录态。本次开发工作区没有 opencli/Collector，因此未把真实平台抓取标为当前环境已验证。
- **仍是单分析师工具**：SQLite 单写者、远程 OAuth/飞书/LLM/Embedding 依赖配置；生产无人值守、第三方数据源和组织级能力尚待接入或验收。
- 详细矩阵与证据见 👉 [`docs/功能可用性清单.md`](docs/功能可用性清单.md)。

## 快速开始

面向舆情分析师的完整页面和日常值班说明见 [`docs/用户操作指南.md`](docs/用户操作指南.md)；功能边界和核验证据见 [`docs/功能可用性清单.md`](docs/功能可用性清单.md)。

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

### UI 入口

- `/`：唯一生产工作台入口，资源位于 `yuqing/web/workbench/`。
- `/v2`：指向同一工作台的兼容 URL，不维护第二份 HTML。
- `/legacy`、`/dash`、`/exec`：服务端渲染的回退及专项视图。

历史静态原型不参与 Python 包、Docker 镜像或运行时路由，避免出现多份页面各自演进。
原型归档及版本对应关系见 [`docs/ui/README.md`](docs/ui/README.md)。

**语义向量化**（配置页填 `EMBED_API_KEY`=阿里百炼即启用，无 key 全部降级回词汇匹配、不阻塞）：语义检索（问"电池"召回只说"续航"的帖）· 话题语义归并（"续航差"+"电池不耐用"归一簇）· 洗稿近似去重（改写控评识别为同簇）· 监控目标语义扩展（`cli suggest` 从数据发现该监控的新词，人工确认）。可选语义相关性过滤 `SEMANTIC_RELEVANCE=1`（召回不含品牌字面的相关帖，默认关）。

监控对象配置在 [`yuqing/watch.yaml`](yuqing/watch.yaml)（实体/别名/否定词/危机词，git 版本化）。

CCE 生产 Pod 使用两个独立容器：工作台负责 OAuth、SQLite、分析和报告，Collector
sidecar 负责 Chromium、opencli、平台登录和原始抓取。两张镜像独立构建，Collector
不直接写数据库；登录维护通过 `kubectl port-forward` 访问 noVNC。部署边界与操作步骤见
[`deploy/k8s/README.md`](deploy/k8s/README.md)。

## 架构

```
watch.yaml → collect(opencli登录态,混合,全部aliases) → SQLite(raw_observations/clean/document_entities)
           → analyze(规则/Claude,ABSA,证据校验) → score(线性加权风险)
           → analysis_results(版本血缘) → report(数字注入+引用校验) / incidents(P0/P1待确认→升级)
           → 飞书 + 鉴权工作台 ；贯穿：健康三态+静默失败熔断
```

| 模块 | 职责 |
|---|---|
| `collect.py` / `collection/` | 兼容门面；外部抓取、语义相关性、过滤持久化、健康审计和全局编排分阶段实现 |
| `normalization.py` | Collector/opencli 原始字段归一化为稳定 `CleanDoc` 契约 |
| `store.py` / `storage/` | `Store` 兼容门面；Schema、文档、运行事件、人工复核按仓储边界拆分 |
| `analyze.py` | 情绪/ABSA/信息抽取（规则 stub + Claude tool use），evidence 逐字校验 |
| `score.py` | 线性加权风险分（平台×情绪×危机×影响力，可解释） |
| `alerts.py` | P0/P1 实时预警，事件簇冷却，竞品不误告警 |
| `analytics.py` / `analytics_*` | 兼容门面；时序、健康指数、语义聚类和主动学习分域实现 |
| `report.py` / `reporting/` | 兼容门面；聚合、Markdown 成文、引用校验和飞书投递分阶段实现 |
| `insights.py` | 老板日报 / AI 问答 / 诉求→需求 / 事件时间线 |
| `dashboard.py` | 看板兼容门面、OAuth/会话策略和服务启动入口 |
| `dashboard_http.py` / `dashboard_http_parts/` | stdlib HTTP 兼容门面；响应、鉴权、GET/POST legacy 路由分层 |
| `dashboard_api_v1.py` / `dashboard_context.py` / `dashboard_routes/` | `/api/v1/*` 请求上下文与领域路由注册表 |
| `dashboard_views.py` / `dashboard_legacy/` | 旧版 URL 的兼容门面；HTML 页面按职责拆分，新 UI 只在 workbench 演进 |
| `dashboard_runtime.py` | 单机后台跑批状态、互斥启动和协作式停止 |
| `watch_config.py` | `watch.yaml` 路径、加载与校验的单一配置边界；包根仅保留兼容导出 |
| `health.py` `budget.py` | 数据健康三态 / 成本配额熔断 |
| `selfcheck.py` | 端到端离线自检（是改代码后的验收基准） |

### Graphify 架构图

项目内置 Graphify 技能和运行时图生成脚本。`graphify-out/`、`graphify-runtime/`
属于本地生成产物，不提交仓库。安装 Graphify 后可重新生成：

```bash
graphify update .
"$(sed -n '1p' graphify-out/.graphify_python)" scripts/build_runtime_graph.py
```

生成结果中，`graphify-runtime/graph.html` 是模块级有向依赖图，
`graphify-runtime/callgraph.html` 是函数级调用图。

## 路线图（详见设计文档第十章）

| 阶段 | 目标 | 状态 |
|---|---|---|
| 0 现状 | 真实数据跑通 | ✅ 已完成 |
| 1 内部可用 MVP | 可信数据地基 + 工作台闭环（串味过滤/复核队列/可信度标记） | ✅ 代码已交付；生产部署、无人值守和分析师验收待完成 |
| 2 部门推广 | 多用户分角色（Postgres/RBAC/部门看板） | 规划 |
| 3 全公司铺开 | 高层可靠 + P0 危机 SLA | 规划 |

## 诚实的限制

- **数据质量是命门**：关键词易串味（搜 "Youdoo Box" 会串出 "Doo Prime" 外汇/创维新闻）→ 靠 `must_not` + 人工复核收紧（v1 已落地）。
- **微博搜索无互动数**（点赞/转发/粉丝）→ 影响力加权退化，报告标 ⚠降级；**抖音搜索带真实互动**（plays/likes/comments/shares）不降级。
- **平台采集依赖外部运行条件**：小红书/抖音/B站等按平台要求维护登录态，黑猫走浏览器桥；登录墙、覆盖和账号失效会使结果降级，零结果不能直接解释为没有舆情。
- **单用户**：SQLite 单写者，多用户需迁 Postgres（阶段 2）。
- **报告链接是 127.0.0.1**：仅本机浏览器可看；手机/多人需部署到可访问地址（配置页 DASHBOARD_URL）。
- 情绪判定含中文反讽误判风险，关键负面结论建议人工抽检。

## 许可 / 合规

内部工具。采集仅限公开数据、限速错峰、只用可牺牲的专用监控号；对外交付默认脱敏聚合，遵循 PIPL 与平台 ToS（合规待法务背书）。
