# 一期产品化集成设计

## 1. 技术决策

一期沿用现有模块化单体：

```text
浏览器工作台
  ├─ HTML / CSS / 原生 JavaScript
  └─ /api/v1/* JSON
          ↓
ThreadingHTTPServer + OAuth/CSRF
          ↓
Store / analytics / insights / alerts / report / collect
          ↓
SQLite WAL
```

不引入新的 Web 框架和数据库。先验证工作台是否能被单个分析师稳定使用；出现第二个并发写角色后，再评估 FastAPI、Postgres 和正式 SPA。

## 2. 前端工程结构

将当前单文件原型拆成可维护的包内静态资源：

```text
yuqing/web/workbench/
  index.html
  styles.css
  app.js
  api.js
  state.js
  views/
    overview.js
    collect.js
    review.js
    analysis.js
    alerts.js
    backlog.js
    reports.js
    watch.js
```

`dashboard.py` 提供：

- `GET /`、`GET /v2` → `index.html`
- `GET /v2/assets/*` → 静态资源
- `GET /legacy` → 旧版首页
- `GET/POST /api/v1/*` → JSON API

同时更新 `pyproject.toml` package-data 和 Docker 构建验证，确保资源进入 wheel 和镜像。

前端只负责展示、筛选条件和交互状态，不在浏览器中计算 BHI、风险等级、ABSA、SOV 或报告数字。

## 3. 通用 API 契约

### 成功响应

```json
{
  "success": true,
  "data": {},
  "meta": {
    "generated_at": "2026-07-13T13:00:00+08:00",
    "entity_id": "youdoo",
    "data_quality": "ok"
  }
}
```

### 失败响应

```json
{
  "success": false,
  "error": {
    "code": "INVALID_TRANSITION",
    "message": "当前状态不能执行该操作"
  }
}
```

规则：

- 时间统一 ISO 8601，带时区。
- 枚举值由后端输出，前端不得根据中文文案反推状态。
- 数字缺失使用 `null`，不以 `0` 冒充。
- 数据不完整时必须返回 `data_quality` 和 `quality_notes`。
- 写接口继续使用现有 `_mutation_allowed`；敏感配置继续使用 `_write_allowed`。
- `/api/v1` 接口不返回密钥、Cookie、原始配置文件路径。

## 4. 页面与接口

### 4.1 总览工作台

`GET /api/v1/overview?entity_id={id}&range=7d`

数据来源：`analytics.brand_health`、趋势聚合、平台健康、incidents、review 队列。

返回：

- 总声量、BHI、负面数、进行中预警数
- 情感趋势
- 最高优先级待确认/处理中 incident
- 待复核数量
- 数据质量摘要

不返回前端模拟的“固定今日待办”。待办由真实 incident、review 和最新报告状态推导。

### 4.2 采集接入

- `GET /api/v1/collection/status`
- `POST /api/v1/collection/run`
- `POST /api/v1/collection/stop`
- `GET /api/v1/collection/login-status`

复用现有 `/api/run*`、`/api/login/status`，增加统一响应和平台级统计。页面必须明确显示“采集执行环境”，避免云端看板与本机 Chrome 采集能力混淆。

### 4.3 数据质检

- `GET /api/v1/reviews?entity_id=&status=&platform=&confidence=&limit=&cursor=`
- `POST /api/v1/reviews/{doc_id}`
- `POST /api/v1/reviews/batch`

写入现有 `review` 表。批量请求必须限制最大 100 条，并返回逐条结果。列表必须提供正文、平台、作者、机器标签、置信度、风险分、进入队列原因和原帖 URL。

### 4.4 情绪分析

`GET /api/v1/analysis?entity_id={id}&range=7d`

复用 `chart_data` 和 `analytics`，返回：

- 情感趋势
- ABSA 维度聚合
- 主要话题
- BHI 趋势
- 样本量与可信度说明

### 4.5 预警中心

- `GET /api/v1/incidents?status=&entity_id=`
- `GET /api/v1/incidents/{incident_id}`
- `POST /api/v1/incidents/{incident_id}/transition`

复用 incidents 状态机。前端只显示后端返回的 `allowed_actions`，不自行猜测合法流转。

### 4.6 诉求管理

- `GET /api/v1/backlog?entity_id=&range=`
- `GET /api/v1/backlog.csv?entity_id=&range=`

复用 `insights.backlog` 和 `backlog_csv`。一期只读，不增加 Roadmap 状态编辑，不自动创建研发工单。

### 4.7 报告中心

- `GET /api/v1/reports`
- `GET /api/v1/reports/{run_id}`
- `POST /api/v1/reports/generate`
- `GET /api/v1/docs/{doc_id}`

报告直接读取 `reports` 表。生成操作调用现有确定性报告链路；一期不实现多人审批和发布状态机。来源链接必须进行协议白名单和 HTML 转义。

### 4.8 监控配置

- `GET /api/v1/watch`
- `PUT /api/v1/watch`
- `GET/POST /api/v1/keywords`
- `GET/POST /api/v1/seeds`

复用现有 watch、keywords、seed 能力。保存前继续执行 YAML 强校验和 `.bak` 备份。

### 4.9 一期隐藏项

- 结构化知识库
- 报告审批流
- 远程系统密钥配置
- 用户角色管理

若保留入口，只显示清晰的“后续阶段”说明，不能执行假保存。

## 5. 后端代码组织

在 `yuqing/dashboard.py` 中只保留路由和鉴权，新增：

```text
yuqing/api/
  responses.py       # 统一响应与错误
  overview.py        # 总览读模型
  collection.py
  reviews.py
  analysis.py
  incidents.py
  backlog.py
  reports.py
  watch.py
```

API 层调用现有领域函数，不复制 SQL 和业务计算。对仍由 dashboard 内部函数提供的数据，先抽成可测试的纯函数，再由旧页面和新 API 共同调用。

## 6. 数据与迁移

一期尽量不增加表。

- `review`：继续追加审计记录；查询时以最新记录作为当前 verdict。
- `reports`：继续按 run_id 保存 Markdown。
- `incidents`：沿用现有状态机。
- backlog：运行时聚合，不持久化 Roadmap 状态。
- schema 自动升级继续由 `Store` 负责，部署前显式执行一次 Store 初始化并检查 `schema_version=2`。

如批量复核查询性能不足，再增加 review 索引；不预建复杂迁移框架。

## 7. 前端状态与交互

- 全局状态只保存：当前实体、当前页面、时间范围、当前用户、请求状态。
- 每个页面进入时独立请求，不在启动阶段一次性加载全站数据。
- GET 请求可使用 30 秒内存缓存；写成功后失效对应页面缓存。
- 所有操作按钮提交期间禁用，防止重复写入。
- 轮询仅用于跑批状态，间隔 2～5 秒；离开采集页后停止。
- 搜索一期只覆盖已获取页面数据；全局跨域搜索延后。

## 8. 安全

- 远程访问必须通过飞书 OAuth。
- 所有修改接口执行 session、Origin、X-Forwarded-Host 和 CSRF 校验。
- `/config` 的密钥写入仍限本机/SSH 隧道。
- watch.yaml、搜索词、用户输入和来源文档统一转义。
- 不将 API 异常堆栈返回浏览器。
- 新增写接口必须包含未登录、恶意 Origin、过期 session 三组回归测试。

## 9. 测试与 CI

### 后端

- API 状态码、响应 schema、分页和筛选测试。
- 每个写接口的成功、非法参数、非法状态和鉴权测试。
- SQLite 旧库自动升级测试。
- 复用真实领域函数，禁止在 API 测试中硬编码另一套计算逻辑。

### 前端

- 静态资源可加载测试。
- API 失败、空数据和无权限状态的 DOM 冒烟测试。
- 核心操作：复核、预警流转、跑批触发。

### CI 阻断命令

```bash
python -m unittest discover -v
python -m yuqing.selfcheck
python -m yuqing.architecture_check
python -m yuqing.scheduler selftest
python -m compileall -q yuqing
```

## 10. 发布策略

1. 新版工作台直接作为 `/` 默认入口，同时保留 `/v2` 兼容入口。
2. 旧版页面保留在 `/legacy`、`/dash`、`/exec`，作为应用内快速回退入口。
3. 每次部署验证 `/auth/login`、`/`、`/api/v1/overview`、`/v2` 静态资源和 Store schema。
4. 保留一版旧镜像，出现阻断问题时可切换旧版路由或快速回滚镜像。
