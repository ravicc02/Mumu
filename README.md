<div align="center">

<img src="assets/brand/mumu.png" width="148" alt="Mumu 应用图标">

<h1>Mumu</h1>

<hr>

**为大学生而造的 刷作业、刷课、签到为一体的辅助学习客户端**

**简体中文** · [English](README.en.md) · [繁體中文](README.zh-TW.md) · [한국어](README.ko.md) · [日本語](README.ja.md) · [Español](README.es.md)

[![CI](https://github.com/ravicc02/Mumu/actions/workflows/ci.yml/badge.svg)](https://github.com/ravicc02/Mumu/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/ravicc02/Mumu?include_prereleases&label=release&color=4054d8)](https://github.com/ravicc02/Mumu/releases)
![Windows x64](https://img.shields.io/badge/Windows-x64-1e293b?logo=windows)

[下载安装](https://github.com/ravicc02/Mumu/releases) · [更新记录](CHANGELOG.md) · [反馈问题](https://github.com/ravicc02/Mumu/issues/new/choose)

<a href="assets/demo/demo.mp4">
  <img src="assets/demo/demo.gif" width="100%" alt="Mumu 任务公司实录演示">
</a>

[观看完整 23 秒实录](assets/demo/demo.mp4)

</div>

## 使用须知

> [!CAUTION]
> Mumu 是面向 Windows PC 的本地学习任务客户端。请在本人账号或已获得明确授权的范围内使用。
>
> 严禁商业代刷、账号租售、绕过平台验证、窃取数据或将本项目用于其他违法违规用途。因使用方式违反学校、平台规则或法律法规产生的账号限制、数据损失及法律责任，由使用者承担。

> [!IMPORTANT]
> Mumu 不代表或隶属于学习通、头歌、知到智慧树、中国大学 MOOC、智慧职教、职教云和雨课堂。相关名称与 Logo 仅用于识别对应服务。
>
> 平台页面和能力会变化，项目不保证所有学校、课程、题型和页面版本持续可用。模型请求会将完成任务所需上下文发送到你配置的模型服务，使用前请确认平台规则、课程要求和数据处理边界。

## 功能介绍

| 功能 | 说明 |
| --- | --- |
| **账号与平台** | 按平台和稳定用户 ID 保存账号，自动同步名称与头像；重复授权更新原记录，不按姓名合并账号 |
| **作业** | 采集未完成且当前可提交的作业；识别题目、生成答案、确定性填写、暂存、评测及提交核验 |
| **网课** | 读取课程及任务点，跳过已完成内容，执行已支持的媒体和阅读流程，并保存平台进度凭据 |
| **签到** | 多账号批量处理；支持签到码、手势、二维码和位置输入，可创建可撤销的批次分享链接 |
| **实时任务浏览器** | 每个任务使用独立浏览器和授权快照；支持实时查看、暂停、重试和人工介入，关闭查看端不会停止任务 |

任务分别使用独立浏览器，允许并行处理不同账号。软件只在平台结果可核验时标记完成，不把模型自报成功、截图存在或部分完成当作业务完成凭据。

## 支持的平台

| 平台 | 作业 | 网课 | 签到 | 当前能力 |
| :---: | :---: | :---: | :---: | --- |
| <img src="assets/platforms/xuexitong.png" height="28" alt="学习通 Logo"><br>**学习通** | 支持 | 支持 | 支持 | 结构化题型、章节学习、批量签到与分享 |
| <img src="assets/platforms/educoder.png" height="28" alt="头歌 Logo"><br>**头歌** | 支持 | — | — | 课堂实验、Java/JUnit 编辑器、自动评测及有限修复 |
| <img src="assets/platforms/zhihuishu.ico" height="28" alt="知到智慧树 Logo"><br>**知到智慧树** | — | 支持 | — | 课程任务点、媒体和阅读流程 |
| <img src="assets/platforms/icourse.ico" height="28" alt="中国大学 MOOC Logo"><br>**中国大学 MOOC** | — | 支持 | — | 课程任务点、媒体和阅读流程 |
| <img src="assets/platforms/icve.ico" height="28" alt="智慧职教 Logo"><br>**智慧职教** | — | 支持 | — | 课程任务点、媒体和阅读流程 |
| <img src="assets/platforms/zjy.ico" height="28" alt="职教云 Logo"><br>**职教云** | — | 支持 | — | 课程任务点、媒体和阅读流程 |
| <img src="assets/platforms/yuketang.ico" height="28" alt="雨课堂 Logo"><br>**雨课堂** | — | 支持 | — | 课程任务点、媒体和阅读流程 |

## Star History

<div align="center">

<a href="https://star-history.com/#ravicc02/mumu&amp;Date">
  <img src="https://api.star-history.com/svg?repos=ravicc02/mumu&amp;type=Date" width="100%" alt="Mumu Star History">
</a>

</div>

## 开始使用

1. 在 [Releases](https://github.com/ravicc02/Mumu/releases) 下载 Windows x64 安装包；发行候选版本标记为 Pre-release。
2. 首次打开进入初始化：检测运行环境；缺少依赖时可点击安装，失败时显示手动命令。
3. 配置模型接口、Key、模型和思考强度，再选择平台授权。两步均可跳过，之后在设置与平台中补充。
4. 选择任务执行；遇到待处理事项时通过原任务浏览器介入，处理后继续。
5. 查看平台核验结果。学习通最终提交与头歌自动评测分别在作业设置中管理。

**从源码运行**

环境：Windows 10/11 x64、Node.js 24、Python 3.13、Chrome 或 Edge。

~~~powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
npm --prefix desktop ci
$env:MUMU_PYTHON = (Resolve-Path .\.venv\Scripts\python.exe).Path
npm start
~~~

网页预览分两个终端运行：

~~~powershell
# 终端一：仓库根目录
.\.venv\Scripts\python.exe backend\run_server.py --host 127.0.0.1 --port 8877

# 终端二：仓库根目录
npm run desktop:web
~~~

前端为 http://127.0.0.1:5174/，本地 API 为 http://127.0.0.1:8877/。开发和构建步骤见[贡献指南](CONTRIBUTING.md)。

## 数据与隐私

默认数据目录继续使用 %LOCALAPPDATA%/AIAssistantStudio，保存授权、任务、SQLite 和截图。该旧目录名是升级兼容标识，不随产品展示名变更。模型请求会将题目及必要上下文发送到使用者配置的模型服务。

~~~text
logs/
  main.log       今日详细日志
  oldmain.log    历史日志归档
~~~

遇到问题，从“设置 → 系统设置”打开日志目录，将脱敏后的 main.log 提供给维护者。公开 Issue 不提交 Cookie、Key、二维码凭据或授权文件，详见[安全与隐私](.github/SECURITY.md)。

## 项目结构

~~~text
desktop/       Vue 与 Electron；src/app 为入口，src/features 按业务域划分，src/shared 放通用控件
backend/       FastAPI、平台适配和任务执行；services 按账户、作业、任务、运行时及通知划分
scripts/       版本、源文件审查、打包、冒烟及发布工具
assets/        README 品牌、演示和平台标识
.github/       CI、Release、问题模板和依赖维护
~~~

## 参与贡献

[贡献指南](CONTRIBUTING.md) · [更新日志](CHANGELOG.md)
