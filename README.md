# AI Assignment Studio

本地 Windows 作业工作台。使用 Electron + Vue 管理多个学习通账号，通过 Playwright 无头采集当前仍可提交的未完成作业，再用 browser-use 驱动可操控 Chrome 完成答题并实时显示 Agent 日志。

登录 Cookie、模型 Key、任务记录和截图都保存在本机，不上传到本项目仓库。

## 功能

- 多账号独立 Playwright Profile 和 `storage-state.json`
- 学习通新版/旧版课程表兼容采集
- 按开课学年和学期筛选未完成、当前仍可提交的作业
- 作业多选和批量创建 Agent 任务
- 每账号任务串行排队，避免多个 Agent 抢占同一个浏览器 Profile
- 可操控 Chrome 窗口，Agent 执行时仍可人工查看和接管
- 任务状态：排队、运行、暂停、停止中、停止、完成、失败、中断
- 暂停、继续、停止和重试
- WebSocket 实时日志、当前 URL、步骤、问题、截图和最终凭证
- 模型供应商、API 地址、Key、模型列表和思考强度配置
- 微信 iLink Bot 扫码绑定和任务完成通知

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
AI_REASONING_EFFORT=medium
ANSWER_STUDIO_PORT=8877
```

`.env` 已被 Git 忽略，不要把真实 Key 写进源码或提交到远端。

## 用户怎么用

1. 进入“平台”，点击“添加”创建学习通账号。
2. 点击账号右侧“...”，选择“刷新 Cookie”，在弹出的 Chrome 中完成登录。
3. 回到“平台”，选择学年/学期后点击“采集”。
4. 在作业列表勾选一个或多个当前可提交作业，点击“启动 Agent”或“批量启动 Agent”。
5. 在“面板”右侧查看实时状态、URL、步骤日志和截图。
6. 运行中可暂停、继续或停止；失败或中断后可重试。
7. 在“执行凭证”中打开步骤截图、最终截图和 `result.json`。

默认不点击最终提交。只有勾选“允许 Agent 最终提交”后，任务提示词才允许提交。建议保留人工核对。

## 本地开发

```powershell
cd E:\Desktop\workshop\ai-quiz-assistant
python -m pip install -r backend/requirements.txt
python -m playwright install chromium

cd desktop
npm install
npm run dev
```

Electron 会启动本地 FastAPI：`http://127.0.0.1:8877`。只预览前端时运行：

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
GET  /api/accounts/{account_id}/preview
POST /api/accounts/{account_id}/collect?academic_year=2026
GET  /api/assignments?account_id=...&academic_year=2026&submittable=true
POST /api/jobs
POST /api/jobs/{job_id}/pause
POST /api/jobs/{job_id}/resume
POST /api/jobs/{job_id}/stop
POST /api/jobs/{job_id}/retry
WS   /ws/jobs/{job_id}
```

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
