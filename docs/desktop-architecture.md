# Windows 本地答题工作台技术栈

## 决策

| 层 | 技术 | 原因 |
| --- | --- | --- |
| Windows 桌面壳 | Electron | Node/Python 启动逻辑简单，Windows 打包成熟，适合本地 Web 控制台 |
| 前端 | Vue 3 + Vite | 状态驱动界面轻量，适合账号、作业、任务和实时监控 |
| 本地 API | FastAPI | 与 browser-use、Playwright 同为 Python，支持 WebSocket 和异步任务 |
| 浏览器自动化 | Playwright | 多账号持久化目录、Cookie 复用、无头采集、截图和下载都成熟 |
| Agent | browser-use | 直接使用自然语言操作页面，适合不同题型和页面变化 |
| 数据 | SQLite | 单机无需额外服务，便于备份和审计 |
| 实时画面 | WebSocket | Agent 每步截图和日志即时显示在任务面板 |
| 打包 | PyInstaller + electron-builder | Python 后端与 Electron 壳分别构建，最终生成 NSIS 安装包 |
| 推送 | 微信 iLink Bot | 完成后推送结果；微信协议参考 `pws-cc` |

## 进程结构

```text
AI Assignment Studio.exe
├── Electron main
│   ├── 启动 answer-studio-backend.exe
│   └── 加载 Vue 控制台
├── FastAPI :8877
│   ├── SQLite
│   ├── Playwright profiles
│   ├── 学习通无头采集
│   ├── 到期自动检测
│   ├── 本机 Chrome + CDP
│   ├── browser-use Agent
│   └── WebSocket events
└── 本地数据
    ├── profiles/<account-id>
    ├── artifacts/<job-id>
    └── logs/
```

## 登录态

每个账号独立保存：

- Playwright `user_data_dir`
- `storage-state.json`
- 登录状态与最近登录时间

不同账号不共享 Cookie，同一账号的采集任务和 Agent 任务复用其浏览器资料。
Agent 启动后会先把 `storage-state.json` 中的 Cookie 和本地存储注入 CDP 浏览器，再打开学习通个人空间验证登录态；验证失败会直接终止任务并标记账号授权失效。
进入 UEditor 作业页时会通过页面初始化脚本隐藏编辑工具栏和底部装饰控件，保留编辑器正文 iframe，降低模型结构化输出失败概率。

## 作业采集

1. 使用账号的 Playwright 持久化浏览器上下文。
2. 打开课程中心页面并解析渲染后的 HTML 与 iframe。
3. 解析 `#frame_content` 和 `#courseList`。
4. 筛选指定学年开课的课程。
5. 从课程页读取 `#enc`、`#t`、`#workEnc`。
6. 请求 `/mooc2/work/list` 并解析作业状态。
7. 对未交作业请求 task 页面，判断是否仍存在有效提交入口。
8. 每次采集创建 `collection_runs` 快照，记录课程总数、处理数、失败课程和作业数。
9. 成功快照停用本轮未出现的旧作业；部分失败时保留失败课程的上一版数据。

该流程不需要打开课程标签页。

## Agent 任务

- 同一账号的任务通过异步锁串行执行，后续任务保持排队状态。
- Agent 启动前先关闭该账号的预览窗口，再由应用启动本机 Chrome。
- browser-use 通过 CDP 连接该 Chrome，直接复用账号持久化 `user_data_dir` 中的登录态。
- 每步通过 CDP 保存截图、动作记录和当前 URL。
- 任务执行结果与作业提交结果分开记录；Agent 自报未完成时进入 `incomplete`，不会伪装成成功。
- 任务心跳超过 2 分钟会写入 `stalled` 事件；停止超时后强制取消执行任务。
- 重试会记录 `parent_job_id` 和递增的 `attempt`。
- 人工可在任务结束后确认 `submitted_verified` 或 `not_submitted`。
- DeepSeek 使用 JSON Object 输出、可配置的第二模型 Fallback，并绕过 Windows WMI 平台探测阻塞。
- 默认不点击最终提交。
- 完成后保存最终截图、Agent 历史和结果 JSON。

## 当前边界

- 已完成学习通适配器接口和多平台注册层，但仅实现学习通。
- 微信连接器支持二维码获取、扫码状态轮询和会话刷新；真实账号扫码仍需发布前复验。
- 自动检测仅在桌面应用和后端运行时执行，不会在应用关闭后后台驻留。
- Python sidecar 打包脚本已提供，正式发布前仍需在干净 Windows 环境验证安装包。
