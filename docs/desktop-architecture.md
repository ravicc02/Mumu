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
- 任务执行结果与作业提交结果分开记录；Agent 自报未完成时进入 `needs_intervention`，不会伪装成成功。
- `needs_intervention` 保留 Chrome、BrowserSession、截图流和账号锁，用户可从平台页直接切到浏览器继续操作。
- 用户点击“结束介入”后，浏览器关闭、账号锁释放、任务转为 `stopped`，再由人工核对作业提交状态。
- 任务心跳超过 2 分钟会写入 `stalled` 事件；停止超时后强制取消执行任务。
- 重试会记录 `parent_job_id` 和递增的 `attempt`。
- 人工可在任务结束后确认 `submitted_verified` 或 `not_submitted`。
- DeepSeek 使用原生 function calling 约束 `AgentOutput`，保留可配置的第二模型 Fallback，并绕过 Windows WMI 平台探测阻塞。
- 默认不点击最终提交。
- 完成后保存最终截图、Agent 历史和结果 JSON。

### 学习通结构化题型适配

学习通 `mooc2/work/task` 页面优先进入结构化答题流程，不进行几十步自由探索：

1. 打开作业页并等待 UEditor 初始化。
2. 一次性提取全部 `.questionLi`，识别 48 类作答单元，包括单选、多选、填空、判断、简答、共用选项、组合题和完形填空。
3. 将题号、题型、题干、选项 key 和文本字段数量发送给模型，返回严格的 `QuizAnswerSheet`。
4. 用确定性 DOM 适配脚本写入：
   - `div[role=radio/checkbox]` 点击标准选项。
   - `span[data]` 点击共用选项和组合题选项；共用选项题每次点击后重新查询 DOM，避免页面重渲染导致后续小组丢失。
   - `textarea` 与对应 UEditor iframe 同步文本，并调用 `UE.setContent/sync`。
   - 共用选项题写入隐藏 `answer<question-id>` JSON。
5. 核验所有选择题隐藏值以及所有填空/简答字段，并将所有已作答题目的 `answerSheet<question-id>` 标记为 `active`，保证侧边答题卡与实际答案一致。
6. 任一单元未写入则任务失败，不标记完成。
7. 全部通过后进入 `needs_intervention`，保留浏览器供人工核对，不点击最终提交。

## 平台页状态机

平台页固定分为四栏：

1. 平台和账号：账号授权、Cookie 刷新、重命名、删除。
2. 作业：学期筛选、采集、多选、批量执行和单作业执行。
3. 大尺寸实时浏览器：显示 Agent 当前截图；可操控任务可进入同一个真实 Chrome。
4. 实时日志：展示宏观流程状态机和逐条 WebSocket 事件。

“进入实时浏览器”通过 CDP 恢复 Chrome 窗口并将目标页切到前台，不重启浏览器、不暂停 Agent。运行中由 Agent 的步骤截图更新页面预览；暂停和待介入状态下才轮询 `/api/jobs/{id}/snapshot`，避免实时快照与 browser-use 的 DOM/截图 watchdog 争抢 CDP。

任务日志状态机：

```text
任务排队 -> 登录与浏览器 -> Agent 初始化 -> 页面分析 -> 执行步骤 -> 结果确认
```

每个步骤事件记录 Agent 的结构化动作，例如点击、输入、跳转、按键和上传。日志条目同时显示步骤号、事件状态、动作详情和时间。任务进入 `needs_intervention` 时，日志状态机把“执行步骤”和“结果确认”标记为待处理，并继续订阅同一个 WebSocket。

## 当前边界

- 已完成学习通适配器接口和多平台注册层，但仅实现学习通。
- 微信连接器支持二维码获取、扫码状态轮询和会话刷新；真实账号扫码仍需发布前复验。
- 自动检测仅在桌面应用和后端运行时执行，不会在应用关闭后后台驻留。
- Python sidecar 打包脚本已提供，正式发布前仍需在干净 Windows 环境验证安装包。
