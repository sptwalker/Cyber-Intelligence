# UI 原型归档

本目录保存界面方案的历史原型，用于设计对照，不参与 Python wheel、Docker 镜像或生产路由。
正式工作台位于 `yuqing/web/workbench/`，功能应在正式工作台中逐步接入真实 API。

| 文件 | 定位 | 运行依赖 | 使用建议 |
|---|---|---|---|
| `prototype-v1.html` | 早期专业看板方案 | Vue、Ant Design、ECharts CDN | 仅用于回看早期信息架构 |
| `prototype-v2.html` | 完整舆情运营工作台原型 | Google Fonts，其余逻辑内联 | 用于对照完整页面和交互覆盖 |
| `prototype-v3.html` | V1 可视化与 V2 工作台的融合优化版 | Google Fonts，其余逻辑内联 | 当前 UI 设计参考 |

这些文件都是完整 HTML 页面，包含各自的全局样式和脚本，不能直接拼接成一个文件。
需要采纳其中的设计时，应按页面或组件迁移到正式工作台，并连接现有 `/api/v1/*` 接口。

本地预览可直接打开对应 HTML；其中 V1 的外部 CDN 资源需要网络连接。
