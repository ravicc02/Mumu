<div align="center">

<img src="assets/brand/mumu-loop.gif" width="148" alt="Mumu animated mascot">

<h1>Mumu</h1>

**An all-in-one client that supports university students with campus life and learning.**

[简体中文](README.md) · **English** · [繁體中文](README.zh-TW.md) · [한국어](README.ko.md) · [日本語](README.ja.md) · [Español](README.es.md)

[![CI](https://github.com/ravicc02/Mumu/actions/workflows/ci.yml/badge.svg)](https://github.com/ravicc02/Mumu/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/ravicc02/Mumu?include_prereleases&label=release&color=4054d8)](https://github.com/ravicc02/Mumu/releases)
![Windows x64](https://img.shields.io/badge/Windows-x64-1e293b?logo=windows)

[Download](https://github.com/ravicc02/Mumu/releases) · [Changelog](CHANGELOG.md) · [Issues](https://github.com/ravicc02/Mumu/issues/new/choose)

<a href="assets/demo/demo.mp4">
  <img src="assets/demo/demo.gif" width="100%" alt="Mumu task workspace demonstration">
</a>

[Watch the full 23-second demo](assets/demo/demo.mp4)

</div>

## Usage Notice

> [!CAUTION]
> Mumu is a local Windows learning-task client. Use it only with your own accounts or accounts you are explicitly authorized to manage.
>
> Do not use this project for commercial task farming, account resale, bypassing platform verification, data theft, or other unlawful activity. You are responsible for account restrictions, data loss, and legal consequences caused by use that violates school or platform rules.

> [!IMPORTANT]
> Mumu is not affiliated with or endorsed by Xuexitong, EduCoder, Zhihuishu, ICourse163, ICVE, ZJY, or Yuketang. Names and logos identify the connected services only.
>
> Platform pages and capabilities may change. Mumu does not guarantee support for every school, course, question type, or page version. Model requests send task context to the provider you configure. Confirm platform rules and data-handling requirements before use.

### Disclaimer

> This project is intended only for technical research and learning exchange. Do not use it in any scenario that violates platform rules, school regulations, or applicable laws and regulations. Users are responsible for the consequences of their use.

## Features

| Feature | Description |
| --- | --- |
| **Accounts and platforms** | Save accounts by platform and stable user ID, sync names and avatars, update existing records after reauthorization, and never merge accounts by display name |
| **Assignments** | Collect unfinished and currently submittable work, identify question types, generate answers, fill deterministically, save drafts, evaluate, and verify submission |
| **Online courses** | Read courses and task points, skip completed content, run supported media and reading flows, and retain platform progress evidence |
| **Attendance** | Process multiple accounts in batches; support codes, gestures, QR codes, and location input, with revocable batch sharing links |
| **Live task browser** | Give every task an isolated browser and authorization snapshot; support live viewing, pause, retry, and manual intervention without stopping the task when a viewer closes |

Tasks run in separate browsers and may process different accounts in parallel. Mumu marks a task complete only after a verifiable platform result; model claims, screenshots, and partial completion are not treated as completion evidence.

## Supported Platforms

| Platform | Assignments | Courses | Attendance |
| :---: | :---: | :---: | :---: |
| <img src="assets/platforms/xuexitong.png" height="28" alt="Xuexitong logo"><br>**Xuexitong** | Yes | Yes | Yes |
| <img src="assets/platforms/educoder.png" height="28" alt="EduCoder logo"><br>**EduCoder** | Yes | — | — |
| <img src="assets/platforms/zhihuishu.ico" height="28" alt="Zhihuishu logo"><br>**Zhihuishu** | — | Yes | — |
| <img src="assets/platforms/icourse.ico" height="28" alt="ICourse163 logo"><br>**ICourse163** | — | Yes | — |
| <img src="assets/platforms/icve.ico" height="28" alt="ICVE logo"><br>**ICVE** | — | Yes | — |
| <img src="assets/platforms/zjy.ico" height="28" alt="ZJY logo"><br>**ZJY** | — | Yes | — |
| <img src="assets/platforms/yuketang.ico" height="28" alt="Yuketang logo"><br>**Yuketang** | — | Yes | — |

## Getting Started

1. Download the Windows x64 installer from [Releases](https://github.com/ravicc02/Mumu/releases). Release candidates are marked as pre-releases.
2. On first launch, review the environment check. Install missing dependencies when available, or use the displayed manual command if installation fails.
3. Configure the model endpoint, API key, model, and reasoning effort, then authorize a platform account. Either step can be skipped and completed later in Settings.
4. Select a task to run. When Mumu requests intervention, use the original task browser to review the page, then continue.
5. Check the platform verification result. Final submission for Xuexitong and automatic evaluation for EduCoder are configured separately in assignment settings.

**Run From Source**

Supported environment: Windows 10/11 x64, Node.js 24, Python 3.13, and Chrome or Edge.

~~~powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
npm --prefix desktop ci
$env:MUMU_PYTHON = (Resolve-Path .\.venv\Scripts\python.exe).Path
npm start
~~~

To preview the web app, use two terminals:

~~~powershell
# Terminal 1: repository root
.\.venv\Scripts\python.exe backend\run_server.py --host 127.0.0.1 --port 8877

# Terminal 2: repository root
npm run desktop:web
~~~

The frontend runs at http://127.0.0.1:5174/ and the local API at http://127.0.0.1:8877/. See [CONTRIBUTING.md](CONTRIBUTING.md) for development and build instructions.

## Data and Privacy

The default data directory remains %LOCALAPPDATA%/AIAssistantStudio and stores authorization, tasks, SQLite data, and screenshots. The legacy directory name is retained for upgrade compatibility and does not change with the product display name. Model requests send questions and necessary context to the model service configured by the user.

~~~text
logs/
  main.log       Detailed log for today
  oldmain.log    Archived historical logs
~~~

If a problem occurs, open the log directory from Settings and provide a redacted main.log. Public issues must not include cookies, API keys, QR credentials, or authorization files. See [Security](.github/SECURITY.md).

## Project Structure

~~~text
desktop/       Vue and Electron; src/app is the entry, src/features contains business domains, src/shared contains shared controls
backend/       FastAPI, platform adapters, and task execution; services are grouped by account, assignment, task, runtime, and notification domains
scripts/       Versioning, source audit, packaging, smoke tests, and release tools
assets/        Brand, demo, and platform marks used by README
.github/       CI, releases, issue templates, and dependency maintenance
~~~

## Contributing

[Contributing guide](CONTRIBUTING.md) · [Changelog](CHANGELOG.md)

## Star History

<div align="center">

<a href="https://star-history.com/#ravicc02/mumu&amp;Date">
  <img src="https://api.star-history.com/svg?repos=ravicc02/mumu&amp;type=Date" width="100%" alt="Mumu Star History">
</a>

</div>
