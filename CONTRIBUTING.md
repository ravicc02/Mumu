# 开发与贡献

主产品由 desktop + backend 组成，旧扩展、Node 原型及独立演示已移除。

## 环境

| 组件 | 当前基线 |
| --- | --- |
| 系统 | Windows 10/11 x64；Linux x64 实验版使用 Ubuntu 22.04/24.04 或 Debian 12 |
| Node.js | 24 |
| Python | 3.13 |
| 浏览器 | Chrome 或 Edge；测试也可明确指定 Playwright Chromium |
| 前端依赖 | 使用 desktop/package-lock.json 和 npm ci |
| 后端依赖 | backend/requirements.txt；关键自动化依赖已固定版本，其他依赖仍存在范围约束 |

后端依赖尚未形成完整传递依赖锁。发布时使用全新环境安装，并保留该次构建环境记录；不要将源码构建成功描述为跨日期完全可复现。

## 初始化与运行

从仓库根目录执行：

~~~powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
npm --prefix desktop ci
$env:MUMU_PYTHON = (Resolve-Path .\.venv\Scripts\python.exe).Path
npm run desktop:dev
~~~

Electron 开发入口会启动后端。已有 8877 后端时，使用两个独立终端运行网页预览，避免重复启动：

~~~powershell
.\.venv\Scripts\python.exe backend\run_server.py --host 127.0.0.1 --port 8877
npm run desktop:web
~~~

以上两条长期运行命令分别放在两个终端。前端为 5174，后端为 8877；模型在应用设置中配置。

## 测试

~~~powershell
# 版本、源文件审查及发布工具测试
npm run audit:source
npm test

# 桌面端单元测试
npm run test:desktop

# 后端单元与隔离浏览器测试
$env:PYTHONPATH = (Resolve-Path backend).Path
.\.venv\Scripts\python.exe -m unittest discover -s backend\test -v

# 元数据、文档与前端构建
npm run version:check
npm run release:check
npm --prefix desktop run build:web

# Linux x64 原生构建（在 Linux runner 或 Linux 主机执行）
npm run desktop:build:linux
~~~

激活虚拟环境后，也可使用 npm run test:backend。后端浏览器测试使用临时数据目录，不应复用个人 Profile。设置 MUMU_CHROME_PATH 可以明确指定测试浏览器。真实平台验收必须记录授权范围、实际结果及未通过的部分。

界面改动需检查 PC 视口、焦点、长文本、反馈及控制台。3D 场景增加画面非空、动画、镜头、交互和任务容量检查。真实课程截图仅保留在被忽略的本地验收目录，公开预览使用演示数据。

## 修改与提交

1. 检查已有修改和当前分支，限定本次文件范围。
2. 修改实现及必要测试，补充必要说明和 CHANGELOG 的 Unreleased。
3. 运行相关检查；有失败或未实测的部分，如实记录。
4. 仅在明确要求提交时，暂存本次文件并复核暂存差异。

提交格式：type(scope): 具体变化。常用 type 为 feat、fix、docs、test、build、ci、chore。说明应写问题及改变后的行为，不以“优化一下”等模糊描述代替结果。

分支建议使用 feat/、fix/、documentation/ 前缀。主分支为 main。合并前通过 CI；发布只从 main 上已审查的提交打标签。

## 版本与发行

~~~powershell
npm run version:set -- 0.6.0
npm run version:check
~~~

版本同步会更新 VERSION、根包、桌面包及锁文件中的产品元数据，不修改依赖版本。不要使用全仓库文本替换改版本号。

构建安装包、生成发行说明、检查干净提交、验证产物及创建 Release 草稿时，使用仓库内发行脚本并完成严格检查。本地开发检查可以在脏工作区执行；严格发行检查必须使用已提交、完整且干净的检出。

## 许可证与安全

根包和桌面包使用 `MIT`。新增依赖、复制代码或迁移实现前，必须确认许可证允许当前分发和修改方式。不得因为许可证文本公开可得，就把第三方项目名称、仓库地址或归属说明写入产品文档。

需要保留的第三方版权和许可证文本只能进入 `LICENSES.txt` 与 `desktop/public/licenses/OPEN_SOURCE_LICENSES.txt`，两份内容必须一致。MIT 版权与许可通知和第三方许可证义务，按[许可证与第三方声明](.github/LICENSING.md)执行。

安全漏洞不要通过公开 Issue 或 Pull Request 提交。请使用[私密安全报告](https://github.com/ravicc02/Mumu/security/advisories/new)，并遵守[安全策略](.github/SECURITY.md)。安全修复回归应使用自己的测试账号和隔离数据，不执行不可重复的真实平台操作。

## 问题反馈

反馈问题时提供应用版本、复现步骤、期望及实际结果，以及脱敏后的 main.log。仓库 Issue 不粘贴 Cookie、API Key、完整二维码凭据或个人授权文件。截图与日志可按需要私下提供给维护者。
