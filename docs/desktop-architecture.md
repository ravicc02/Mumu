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
沐沐.exe
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

不同账号不共享 Cookie。账号保存授权资料；每个作业、网课和手动查看窗口使用独立 Chrome 目录，并从对应账号加载 Cookie 与 localStorage。
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

- 同一账号的不同作业和网课使用独立浏览器并行执行；同一个作业或课程仍禁止重复启动。
- Agent 启动前先关闭该账号的预览窗口，再由应用启动本机 Chrome。
- browser-use 通过 CDP 连接该 Chrome，直接复用账号持久化 `user_data_dir` 中的登录态。
- Chrome 启动参数不包含不受支持的 `--disable-blink-features=AutomationControlled`；通过 `Page.addScriptToEvaluateOnNewDocument` 覆盖 `navigator.webdriver`。
- 每步通过 CDP 保存截图、动作记录和当前 URL。
- `interrupted` 为红色中断，记录模型、上游、网络或执行基础设施错误；`incomplete` 为红色未完成，记录缺失题目、素材或不支持的格式。
- `needs_intervention` 为黄色待介入，仅用于全部填写但尚待人工审核与提交；`paused` 为灰色人工暂停。
- `completed` 为绿色已完成，要求 `submission_status=submitted_verified`。模型自报成功或仅允许自动提交不能直接转绿。
- `browser_retained` 独立记录 Chrome、BrowserSession 和任务锁的保留状态；待审核或未完成均可保留浏览器。
- “关闭浏览器”释放当前任务的会话和目录，保留任务结果。重启只把仍在执行的任务标记中断，保留待审核及未完成结果。
- 人工确认已提交将任务转为 `completed` 并释放保留会话；仅确认填写结果仍为 `needs_intervention`。
- 任务心跳超过 2 分钟会写入 `stalled` 事件；停止超时后强制取消执行任务。
- 重试会记录 `parent_job_id` 和递增的 `attempt`。
- 可用操作由后端 `actions` 返回，前端不根据任务颜色自行推断；状态转换使用 SQLite 事务，停止与完成不能同时生效。
- 未完成任务可通过 `/continue` 复用保留的浏览器、页面与已有答案；重试先释放旧会话，再创建新尝试。
- 提交结果未知时，创建、批量执行和重试入口均要求先人工核对，防止绕过核验重复执行。
- 人工可在任务结束后确认 `submitted_verified` 或 `not_submitted`。
- DeepSeek 使用原生 function calling 约束 `AgentOutput`，保留可配置的第二模型 Fallback，并绕过 Windows WMI 平台探测阻塞。
- 默认不点击最终提交。
- 完成后保存最终截图、Agent 历史和结果 JSON。

### 学习通结构化题型适配

学习通 `mooc2/work/task` 页面优先进入结构化答题流程，不进行几十步自由探索：

1. 打开作业页并等待 UEditor 初始化。
2. 一次性提取全部 `.questionLi`，拆分单选、多选、填空、判断、简答、共用选项、组合题和完形填空的作答单元。
3. 将题号、题型、题干、选项 key 和文本字段数量发送给模型，返回严格的 `QuizAnswerSheet`。
4. 用确定性 DOM 适配脚本写入：
   - `div[role=radio/checkbox]` 点击标准选项。
   - `span[data]` 点击共用选项和组合题选项；共用选项题每次点击后重新查询 DOM，避免页面重渲染导致后续小组丢失。
   - `textarea` 与对应 UEditor iframe 同步文本，并调用 `UE.setContent/sync`。
   - 共用选项题写入隐藏 `answer<question-id>` JSON。
5. 核验所有选择题隐藏值以及所有填空/简答字段，并将所有已作答题目的 `answerSheet<question-id>` 标记为 `active`，保证侧边答题卡与实际答案一致。
6. 每个单元写入后记录完成数和页面值；单元之间检查暂停与停止。未写入或核验失败时记录具体原因，不标记完成。
7. 仅填写模式全部通过后进入 `needs_intervention`；自动提交模式继续交给 Agent 提交并核验提交结果页。

## 任务结果诊断

每次任务结束会写入 `result_summary` 事件。诊断内容包括：

- 失败或待介入阶段
- 可读原因
- 最后页面 URL
- 最近动作
- 已完成步骤或作答单元数量
- 建议操作

模型协议、登录态、页面脚本、CDP 超时、页面值核验、执行超时和应用重启中断分别使用不同诊断规则。原始异常仍保存在事件数据中，界面优先展示可读摘要。

## 平台与作业工作区

“平台”页管理平台选择、浏览器授权、最近授权记录和删除。学习通登录后自动读取名称、头像和稳定用户 ID，不提供重命名入口。头像由后端验证并缓存，前端使用本地头像接口。账号行提供对应账号的“作业”和“网课”入口。

学习通使用“平台 + 用户 ID”匹配授权记录。重复授权会更新原记录的会话和资料，保留账号 ID、定时配置及业务关联。只有尚未绑定身份且没有业务数据的临时授权记录可以自动移除。重新授权已有记录时，登录用户必须与原身份一致。启动时会使用已有会话补同步旧账号资料，不改写会话文件。

“作业”页独立承接执行流程，分为四个区域：

1. 账号：切换当前处理账号；“管理平台账号”返回平台授权页。
2. 作业：学期筛选、采集、多选、批量执行和单作业执行。
3. 大尺寸实时浏览器：显示 Agent 当前截图；可操控任务可进入同一个真实 Chrome。
4. 实时日志：展示宏观流程状态机和逐条 WebSocket 事件。

“进入实时浏览器”通过 CDP 恢复 Chrome 窗口并将目标页切到前台，不重启浏览器、不暂停 Agent。运行中由 Agent 的步骤截图更新页面预览；暂停和待介入状态下才轮询 `/api/jobs/{id}/snapshot`，避免实时快照与 browser-use 的 DOM/截图 watchdog 争抢 CDP。

任务日志状态机：

```text
任务排队 -> 登录与浏览器 -> Agent 初始化 -> 页面分析 -> 执行步骤 -> 结果确认
```

每个步骤事件记录 Agent 的结构化动作，例如点击、输入、跳转、按键和上传。日志条目显示步骤号、状态、动作详情和时间。全部填写后“执行步骤”显示完成，“结果确认”根据待审核、未完成、中断或提交成功显示对应状态。

持久化 `phase`、`completed_units` 和 `total_units`，让刷新页面后仍可恢复进度。WebSocket 使用 `after_id` 续传；订阅队列溢出时要求客户端重载历史。重连后重新读取任务状态和凭证；截图图片不重复写入事件数据库，最新截图以独立凭证保存。

状态转换、账号资源与验证范围见 [任务流程](task-workflow.md)。

## 当前边界

- 已完成学习通适配器接口和多平台注册层，但仅实现学习通。
- 微信连接器支持二维码获取、扫码状态轮询和会话刷新；真实账号扫码仍需发布前复验。
- 自动检测仅在桌面应用和后端运行时执行，不会在应用关闭后后台驻留。
- Python sidecar 打包脚本已提供，正式发布前仍需在干净 Windows 环境验证安装包。
