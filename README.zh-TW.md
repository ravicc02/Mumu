<div align="center">

<img src="assets/brand/mumu-loop.gif" width="148" alt="Mumu 動態形象">

<h1>Mumu</h1>

**輔助大學生校園生活以及學習的一體式用戶端**

[简体中文](README.md) · [English](README.en.md) · **繁體中文** · [한국어](README.ko.md) · [日本語](README.ja.md) · [Español](README.es.md)

[![CI](https://github.com/ravicc02/Mumu/actions/workflows/ci.yml/badge.svg)](https://github.com/ravicc02/Mumu/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/ravicc02/Mumu?include_prereleases&label=release&color=4054d8)](https://github.com/ravicc02/Mumu/releases)
![Windows x64](https://img.shields.io/badge/Windows-x64-1e293b?logo=windows)

[下載安裝](https://github.com/ravicc02/Mumu/releases) · [更新記錄](CHANGELOG.md) · [回報問題](https://github.com/ravicc02/Mumu/issues/new/choose)

<a href="assets/demo/demo.mp4">
  <img src="assets/demo/demo.gif" width="100%" alt="Mumu 任務公司實錄演示">
</a>

[觀看完整 23 秒實錄](assets/demo/demo.mp4)

</div>

## 使用須知

> [!CAUTION]
> Mumu 是面向 Windows PC 的本機學習任務用戶端。請在本人帳號或已取得明確授權的範圍內使用。
>
> 嚴禁商業代刷、帳號租售、繞過平台驗證、竊取資料或將本專案用於其他違法違規用途。因使用方式違反學校、平台規則或法律法規所產生的帳號限制、資料損失及法律責任，由使用者承擔。

> [!IMPORTANT]
> Mumu 不代表或隸屬於學習通、頭歌、知到智慧樹、中國大學 MOOC、智慧職教、職教雲和雨課堂。相關名稱與 Logo 僅用於識別對應服務。
>
> 平台頁面與能力可能變更，專案不保證所有學校、課程、題型與頁面版本持續可用。模型請求會將完成任務所需的上下文傳送至你設定的模型服務，使用前請確認平台規則、課程要求與資料處理邊界。

### 免責聲明

> 本專案僅用於技術研究與學習交流，請勿用於任何違反平台規則、學校管理規定或法律法規的場景。使用者需自行承擔相應責任。

## 功能介紹

| 功能 | 說明 |
| --- | --- |
| **帳號與平台** | 依平台與穩定使用者 ID 儲存帳號，自動同步名稱與頭像；重新授權時更新原記錄，不依顯示名稱合併帳號 |
| **作業** | 採集未完成且目前可提交的作業；識別題型、產生答案、確定性填寫、暫存、評測與提交核驗 |
| **網課** | 讀取課程與任務點，跳過已完成內容，執行已支援的媒體與閱讀流程，並保存平台進度憑證 |
| **簽到** | 多帳號批次處理；支援簽到碼、手勢、QR Code 與位置輸入，可建立可撤銷的批次分享連結 |
| **即時任務瀏覽器** | 每個任務使用獨立瀏覽器與授權快照；支援即時查看、暫停、重試與人工介入，關閉查看端不會停止任務 |

任務使用獨立瀏覽器，可平行處理不同帳號。軟體僅在平台結果可核驗時標記完成，不將模型自報成功、截圖存在或部分完成視為完成憑證。

## 支援的平台

| 平台 | 作業 | 網課 | 簽到 |
| :---: | :---: | :---: | :---: |
| <img src="assets/platforms/xuexitong.png" height="28" alt="學習通 Logo"><br>**學習通** | 支援 | 支援 | 支援 |
| <img src="assets/platforms/educoder.png" height="28" alt="頭歌 Logo"><br>**頭歌** | 支援 | — | — |
| <img src="assets/platforms/zhihuishu.ico" height="28" alt="知到智慧樹 Logo"><br>**知到智慧樹** | — | 支援 | — |
| <img src="assets/platforms/icourse.ico" height="28" alt="中國大學 MOOC Logo"><br>**中國大學 MOOC** | — | 支援 | — |
| <img src="assets/platforms/icve.ico" height="28" alt="智慧職教 Logo"><br>**智慧職教** | — | 支援 | — |
| <img src="assets/platforms/zjy.ico" height="28" alt="職教雲 Logo"><br>**職教雲** | — | 支援 | — |
| <img src="assets/platforms/yuketang.ico" height="28" alt="雨課堂 Logo"><br>**雨課堂** | — | 支援 | — |

## 開始使用

1. 從 [Releases](https://github.com/ravicc02/Mumu/releases) 下載 Windows x64 安裝程式；發行候選版本標記為 Pre-release。
2. 首次開啟時完成環境檢查；缺少相依元件時可依提示安裝，安裝失敗時使用手動命令。
3. 設定模型介面、Key、模型與思考強度，再選擇平台授權。兩步均可跳過，之後在設定與平台頁補充。
4. 選擇任務執行；遇到待處理事項時，透過原任務瀏覽器確認並處理後繼續。
5. 查看平台核驗結果。學習通最終提交與頭歌自動評測分別在作業設定中管理。

**從原始碼執行**

支援環境：Windows 10/11 x64、Node.js 24、Python 3.13、Chrome 或 Edge。

~~~powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
npm --prefix desktop ci
$env:MUMU_PYTHON = (Resolve-Path .\.venv\Scripts\python.exe).Path
npm start
~~~

網頁預覽需要兩個終端機：

~~~powershell
# 終端機一：專案根目錄
.\.venv\Scripts\python.exe backend\run_server.py --host 127.0.0.1 --port 8877

# 終端機二：專案根目錄
npm run desktop:web
~~~

前端位於 http://127.0.0.1:5174/，本機 API 位於 http://127.0.0.1:8877/。開發與建置流程見 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 資料與隱私

預設資料目錄仍為 %LOCALAPPDATA%/AIAssistantStudio，保存授權、任務、SQLite 與截圖。舊目錄名稱作為升級相容標識保留，不隨產品顯示名稱變更。模型請求會將題目與必要上下文傳送至使用者設定的模型服務。

~~~text
logs/
  main.log       今日詳細記錄
  oldmain.log    歷史記錄封存
~~~

遇到問題時，請從設定開啟日誌目錄，並提供已脫敏的 main.log。公開 Issue 不得包含 Cookie、API Key、QR 憑證或授權檔案，詳見[安全與隱私](.github/SECURITY.md)。

## 專案結構

~~~text
desktop/       Vue 與 Electron；src/app 為入口，src/features 依業務域劃分，src/shared 放置共用控制項
backend/       FastAPI、平台適配與任務執行；services 依帳號、作業、任務、執行環境與通知劃分
scripts/       版本、原始碼審查、打包、冒煙與發佈工具
assets/        README 使用的品牌、演示與平台標識
.github/       CI、Release、Issue 範本與相依維護
~~~

## 參與貢獻

[貢獻指南](CONTRIBUTING.md) · [更新日誌](CHANGELOG.md)

## Star History

<div align="center">

<a href="https://star-history.com/#ravicc02/mumu&amp;Date">
  <img src="https://api.star-history.com/svg?repos=ravicc02/mumu&amp;type=Date" width="100%" alt="Mumu Star History">
</a>

</div>
