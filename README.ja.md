<div align="center">

<img src="assets/brand/mumu-loop.gif" width="148" alt="Mumu の動くキャラクター">

<h1>Mumu</h1>

**大学生のキャンパスライフと学習を支援するオールインワンクライアント**

[简体中文](README.md) · [English](README.en.md) · [繁體中文](README.zh-TW.md) · [한국어](README.ko.md) · **日本語** · [Español](README.es.md)

[![CI](https://github.com/ravicc02/Mumu/actions/workflows/ci.yml/badge.svg)](https://github.com/ravicc02/Mumu/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/ravicc02/Mumu?include_prereleases&label=release&color=4054d8)](https://github.com/ravicc02/Mumu/releases)
![Windows x64](https://img.shields.io/badge/Windows-x64-1e293b?logo=windows)

[ダウンロード](https://github.com/ravicc02/Mumu/releases) · [更新履歴](CHANGELOG.md) · [問題を報告](https://github.com/ravicc02/Mumu/issues/new/choose)

<a href="assets/demo/demo.mp4">
  <img src="assets/demo/demo.gif" width="100%" alt="Mumu タスクワークスペースのデモ">
</a>

[23 秒の完全なデモを見る](assets/demo/demo.mp4)

</div>

## 使用上の注意

> [!CAUTION]
> Mumu は Windows PC 向けのローカル学習タスククライアントです。本人のアカウント、または管理を明示的に許可されたアカウントでのみ使用してください。
>
> 商業的な代理実行、アカウント販売、プラットフォーム検証の回避、データ窃取、その他の違法行為に本プロジェクトを使用しないでください。学校やプラットフォームの規則に違反する使用によって生じたアカウント制限、データ損失、法的責任は利用者が負います。

> [!IMPORTANT]
> Mumu は Xuexitong、EduCoder、Zhihuishu、ICourse163、ICVE、ZJY、Yuketang と提携しておらず、公式な承認も受けていません。名称とロゴは対象サービスを識別するためだけに使用しています。
>
> プラットフォームのページと対応機能は変更される場合があります。すべての学校、コース、問題形式、ページバージョンでの継続利用を保証するものではありません。モデルリクエストは、利用者が設定したプロバイダーへタスクのコンテキストを送信します。使用前にプラットフォームの規則とデータ処理要件を確認してください。

### 免責事項

> 本プロジェクトは技術研究と学習交流のみを目的としています。プラットフォームの規則、学校の管理規定、法令に違反するいかなる場面でも使用しないでください。利用者はそれに伴う責任を自ら負うものとします。

## 機能紹介

| 機能 | 説明 |
| --- | --- |
| **アカウントとプラットフォーム** | プラットフォームと安定したユーザー ID でアカウントを保存し、名前とプロフィール画像を同期します。再認証時は既存レコードを更新し、表示名だけでアカウントを統合しません |
| **課題** | 未完了かつ現在提出可能な課題を収集し、問題形式の識別、回答生成、決定論的入力、下書き保存、評価、提出検証を行います |
| **オンライン授業** | コースとタスクポイントを読み取り、完了済み内容をスキップし、対応済みのメディア・閲覧フローを実行してプラットフォームの進捗証拠を保存します |
| **出席確認** | 複数アカウントを一括処理し、出席コード、ジェスチャー、QR コード、位置情報入力に対応します。取消可能な一括共有リンクを作成できます |
| **リアルタイムタスクブラウザ** | タスクごとに独立したブラウザと認証スナップショットを使用します。リアルタイム表示、一時停止、再試行、手動介入に対応し、閲覧画面を閉じてもタスクは停止しません |

タスクは個別のブラウザで実行され、異なるアカウントを並行処理できます。Mumu は検証可能なプラットフォーム結果がある場合だけ完了とし、モデルの自己申告、スクリーンショットの存在、部分完了を完了証拠として扱いません。

## 対応プラットフォーム

| プラットフォーム | 課題 | オンライン授業 | 出席確認 |
| :---: | :---: | :---: | :---: |
| <img src="assets/platforms/xuexitong.png" height="28" alt="Xuexitong ロゴ"><br>**Xuexitong** | 対応 | 対応 | 対応 |
| <img src="assets/platforms/educoder.png" height="28" alt="EduCoder ロゴ"><br>**EduCoder** | 対応 | — | — |
| <img src="assets/platforms/zhihuishu.ico" height="28" alt="Zhihuishu ロゴ"><br>**Zhihuishu** | — | 対応 | — |
| <img src="assets/platforms/icourse.ico" height="28" alt="ICourse163 ロゴ"><br>**ICourse163** | — | 対応 | — |
| <img src="assets/platforms/icve.ico" height="28" alt="ICVE ロゴ"><br>**ICVE** | — | 対応 | — |
| <img src="assets/platforms/zjy.ico" height="28" alt="ZJY ロゴ"><br>**ZJY** | — | 対応 | — |
| <img src="assets/platforms/yuketang.ico" height="28" alt="Yuketang ロゴ"><br>**Yuketang** | — | 対応 | — |

## はじめに

1. [Releases](https://github.com/ravicc02/Mumu/releases) から Windows x64 インストーラーをダウンロードします。リリース候補は Pre-release と表示されます。
2. 初回起動時に環境チェックを確認します。不足している依存関係はインストールし、失敗した場合は表示される手動コマンドを使用します。
3. モデルのエンドポイント、API Key、モデル、推論強度を設定し、プラットフォームアカウントを認証します。どちらの手順も後から設定で完了できます。
4. タスクを選択して実行します。Mumu が手動対応を求めた場合は、元のタスクブラウザでページを確認してから続行します。
5. プラットフォームの検証結果を確認します。Xuexitong の最終提出と EduCoder の自動評価は、課題設定で個別に管理します。

**ソースから実行**

対応環境：Windows 10/11 x64、Node.js 24、Python 3.13、Chrome または Edge。

~~~powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
npm --prefix desktop ci
$env:MUMU_PYTHON = (Resolve-Path .\.venv\Scripts\python.exe).Path
npm start
~~~

Web アプリをプレビューするには、2 つのターミナルを使用します。

~~~powershell
# ターミナル 1：リポジトリルート
.\.venv\Scripts\python.exe backend\run_server.py --host 127.0.0.1 --port 8877

# ターミナル 2：リポジトリルート
npm run desktop:web
~~~

フロントエンドは http://127.0.0.1:5174/、ローカル API は http://127.0.0.1:8877/ で動作します。開発とビルドの手順は [CONTRIBUTING.md](CONTRIBUTING.md) を参照してください。

## データとプライバシー

既定のデータディレクトリは引き続き %LOCALAPPDATA%/AIAssistantStudio で、認証、タスク、SQLite データ、スクリーンショットを保存します。旧ディレクトリ名はアップグレード互換性のために維持され、製品の表示名変更に影響されません。モデルリクエストは、問題と必要なコンテキストを利用者が設定したモデルサービスへ送信します。

~~~text
logs/
  main.log       当日の詳細ログ
  oldmain.log    過去ログのアーカイブ
~~~

問題が発生した場合は、設定からログディレクトリを開き、機密情報を除去した main.log を提供してください。公開 Issue には Cookie、API Key、QR 認証情報、認証ファイルを含めないでください。詳細は[セキュリティとプライバシー](.github/SECURITY.md)を参照してください。

## プロジェクト構成

~~~text
desktop/       Vue と Electron；src/app はエントリー、src/features は業務領域、src/shared は共通コントロール
backend/       FastAPI、プラットフォームアダプター、タスク実行；services はアカウント、課題、タスク、ランタイム、通知領域で構成
scripts/       バージョン管理、ソース監査、パッケージング、スモークテスト、リリースツール
assets/        README で使用するブランド、デモ、プラットフォーム表示
.github/       CI、Release、Issue テンプレート、依存関係の保守
~~~

## コントリビューション

[コントリビューションガイド](CONTRIBUTING.md) · [変更履歴](CHANGELOG.md)

## Star History

<div align="center">

<a href="https://star-history.com/#ravicc02/mumu&amp;Date">
  <img src="https://api.star-history.com/svg?repos=ravicc02/mumu&amp;type=Date" width="100%" alt="Mumu Star History">
</a>

</div>
