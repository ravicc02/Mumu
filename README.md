# AI Assignment Studio

本地 Windows 作业工作台。使用 Electron + Vue 管理多个学习通账号，通过 Playwright 无头采集当前仍可提交的未完成作业，再用 browser-use 驱动可操控 Chrome 完成答题并实时显示 Agent 日志。

登录 Cookie、模型 Key、任务记录和截图都保存在本机，不上传到本项目仓库。

## 功能

- 多账号独立 Playwright Profile 和 `storage-state.json`
- 学习通新版/旧版课程表兼容采集
- 按开课学年和学期筛选未完成、当前仍可提交的作业
- 每次采集保存独立快照；课程部分失败时标记“部分采集”，不会伪装成全部成功
- 作业多选和批量创建 Agent 任务
- 每账号任务串行排队，避免多个 Agent 抢占同一个浏览器 Profile
- 可操控 Chrome 窗口，Agent 执行时仍可人工查看和接管
- 任务状态：排队、运行、暂停、停止中、停止、执行完成、未完成、失败、中断
- 独立记录“待人工核对 / 允许提交但未验证 / 已人工确认 / 未提交”等作业结果
- 暂停、继续、停止、超时强制停止和重试；重试保留父子任务与尝试次数
- WebSocket 实时日志、当前 URL、步骤、Agent 截图和最终凭证
- 模型供应商、API 地址、Key、模型列表和思考强度配置
- 主模型失败时使用配置中的第二模型作为真实 Fallback
- 账号级自动检测，可设置 30 分钟到 24 小时的采集间隔
- 微信 iLink Bot 扫码绑定和任务完成、未完成、失败通知

## AI 配置

打开桌面应用左侧的“设置”，点击“配置模型”，填写：

- API 地址，例如 `https://sshzyu.com/v1`
- API Key
- 模型名称，例如 `deepseek-v4.1-flash`
- 思考强度

配置会保存到本地 SQLite。也可以先用项目根目录 `.env` 提供默认值，界面配置优先：

```dotenv
OPENAI_BASE_URL=https://sshzyu.com/v1
OPENAI_API_KEY=
OPENAI_MODEL=deepseek-v4.1-flash
OPENAI_FALLBACK_MODEL=
AI_REASONING_EFFORT=high
ANSWER_STUDIO_PORT=8877
```

`.env` 已被 Git 忽略，不要把真实 Key 写进源码或提交到远端。

对 `deepseek` 模型，桌面端会自动使用兼容 JSON 输出和 Windows WMI 规避逻辑。模型列表第一项是主模型，第二项是实际 Fallback；思考强度会随任务发送给模型。`deepseek-v4.1-flash` 建议使用 `high`，较低强度更容易出现空 JSON 或工具调用格式错误。

## 用户怎么用

1. 进入“平台”，点击“添加”创建学习通账号。
2. 点击账号右侧“...”，选择“刷新 Cookie”，在弹出的 Chrome 中完成登录。
3. 回到“平台”，选择学年/学期后点击“采集”。
4. 在作业列表勾选一个或多个当前可提交作业，点击“启动 Agent”或“批量启动 Agent”。
5. 在“面板”右侧查看实时状态、URL、步骤日志和截图。
6. 运行中可暂停、继续或停止；失败或中断后可重试。
7. 在“执行凭证”中打开步骤截图、最终截图和 `result.json`。
8. 自动提交后回到作业页人工核对，并在任务结果中点击“确认已提交”或“确认未提交”结案。
9. 可在“设置”中为每个账号启用自动检测；应用运行期间按设定间隔刷新作业。

默认不点击最终提交。只有勾选“允许 Agent 最终提交”后，任务提示词才允许提交。建议保留人工核对。

## 本地开发

```powershell
cd E:\Desktop\workshop\ai-quiz-assistant
python -m pip install -r backend/requirements.txt

cd desktop
npm install
npm run dev
```

需要本机安装 Chrome 或 Edge。Electron 会启动本地 FastAPI：`http://127.0.0.1:8877`。只预览前端时运行：

```powershell
cd desktop
npm run web
```

前端地址：`http://127.0.0.1:5174`。

## 数据目录

默认位置：

```text
%LOCALAPPDATA%\AIAssistantStudio
```

包含 SQLite、账号 Profile、浏览器登录态、任务截图、结果 JSON 和日志。该目录不会提交 Git。

## API

核心接口：

```text
GET  /api/health
GET  /api/accounts
POST /api/accounts
POST /api/accounts/{account_id}/login
POST /api/accounts/{account_id}/refresh-session
PATCH /api/accounts/{account_id}/collection-schedule
GET  /api/accounts/{account_id}/preview
POST /api/accounts/{account_id}/collect?academic_year=2026
GET  /api/accounts/{account_id}/collection-runs
GET  /api/collection-runs/{run_id}
GET  /api/assignments?account_id=...&academic_year=2026&submittable=true
POST /api/assignments/{assignment_id}/open
POST /api/jobs
POST /api/jobs/{job_id}/pause
POST /api/jobs/{job_id}/resume
POST /api/jobs/{job_id}/stop
POST /api/jobs/{job_id}/focus-browser
POST /api/jobs/{job_id}/retry
POST /api/jobs/{job_id}/submission-status
WS   /ws/jobs/{job_id}
```

也可以使用外部定时脚本：

```powershell
powershell -ExecutionPolicy Bypass `
  -File scripts/collect-unfinished.ps1 `
  -AcademicYear 2026 `
  -OutputPath "$env:LOCALAPPDATA\AIAssistantStudio\last-collection.json"
```

该脚本可交给 Windows 任务计划程序运行，本地 API 必须处于启动状态。退出码 `0` 表示全部成功，`1` 表示失败，`2` 表示至少一个账号只完成了部分课程采集。

## 验证

```powershell
cd backend
python -m unittest discover -s test -v

cd ..\desktop
npm run build:web
```

## Windows 打包

```powershell
cd desktop
npm run build:win
```

构建流程会生成 Vue 前端、PyInstaller 后端和 electron-builder NSIS 安装包，输出在 `desktop/release/`。

## 使用边界

仅用于自己拥有或明确获授权的页面、课程和内部测试环境。自动答题或自动提交可能违反目标平台条款，使用前应确认授权并在提交前人工核对。

仓库中的 `extension/` 和旧 Node 服务保留为早期 Chrome 扩展原型，当前主要开发方向是 `desktop/` + `backend/`。
