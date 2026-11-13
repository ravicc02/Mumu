<div align="center">

<img src="assets/brand/mumu.png" width="148" alt="Mumu 애플리케이션 아이콘">

<h1>Mumu</h1>

<hr>

**대학생을 위한 과제, 온라인 강의, 출석 통합 학습 보조 클라이언트**

[简体中文](README.md) · [English](README.en.md) · [繁體中文](README.zh-TW.md) · **한국어** · [日本語](README.ja.md) · [Español](README.es.md)

[![CI](https://github.com/ravicc02/Mumu/actions/workflows/ci.yml/badge.svg)](https://github.com/ravicc02/Mumu/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/ravicc02/Mumu?include_prereleases&label=release&color=4054d8)](https://github.com/ravicc02/Mumu/releases)
![Windows x64](https://img.shields.io/badge/Windows-x64-1e293b?logo=windows)

[다운로드](https://github.com/ravicc02/Mumu/releases) · [변경 기록](CHANGELOG.md) · [문제 신고](https://github.com/ravicc02/Mumu/issues/new/choose)

<a href="assets/demo/demo.mp4">
  <img src="assets/demo/demo.gif" width="100%" alt="Mumu 작업 공간 데모">
</a>

[23초 전체 데모 보기](assets/demo/demo.mp4)

</div>

## 사용 안내

> [!CAUTION]
> Mumu는 Windows PC용 로컬 학습 작업 클라이언트입니다. 본인 계정 또는 명시적으로 관리 권한을 받은 계정에서만 사용하세요.
>
> 상업적 대리 수행, 계정 판매, 플랫폼 검증 우회, 데이터 탈취 또는 기타 불법 활동에 이 프로젝트를 사용해서는 안 됩니다. 학교나 플랫폼 규칙을 위반하는 사용으로 발생하는 계정 제한, 데이터 손실 및 법적 책임은 사용자에게 있습니다.

> [!IMPORTANT]
> Mumu는 Xuexitong, EduCoder, Zhihuishu, ICourse163, ICVE, ZJY 또는 Yuketang과 제휴하거나 공식 승인을 받은 프로젝트가 아닙니다. 관련 이름과 로고는 해당 서비스를 식별하기 위한 용도로만 사용합니다.
>
> 플랫폼 페이지와 지원 기능은 변경될 수 있습니다. 모든 학교, 강좌, 문제 유형 및 페이지 버전의 지속적인 지원을 보장하지 않습니다. 모델 요청은 사용자가 설정한 공급자에게 작업 컨텍스트를 전송합니다. 사용 전에 플랫폼 규칙과 데이터 처리 요구 사항을 확인하세요.

## 기능 소개

| 기능 | 설명 |
| --- | --- |
| **계정 및 플랫폼** | 플랫폼과 안정적인 사용자 ID를 기준으로 계정을 저장하고 이름과 프로필 이미지를 동기화합니다. 재인증 시 기존 기록을 갱신하며 표시 이름으로 계정을 병합하지 않습니다 |
| **과제** | 미완료 상태이며 현재 제출 가능한 과제를 수집하고, 문제 유형 식별, 답안 생성, 결정적 입력, 임시 저장, 평가 및 제출 검증을 수행합니다 |
| **온라인 강의** | 강좌와 작업 지점을 읽고 완료된 콘텐츠를 건너뛰며, 지원되는 미디어와 읽기 흐름을 실행하고 플랫폼 진행 증거를 저장합니다 |
| **출석** | 여러 계정을 일괄 처리하며 출석 코드, 제스처, QR 코드 및 위치 입력을 지원합니다. 취소 가능한 일괄 공유 링크를 만들 수 있습니다 |
| **실시간 작업 브라우저** | 각 작업에 독립 브라우저와 인증 스냅샷을 제공합니다. 실시간 보기, 일시 정지, 재시도 및 수동 개입을 지원하며 보기 창을 닫아도 작업은 중지되지 않습니다 |

작업은 별도 브라우저에서 실행되며 서로 다른 계정을 병렬로 처리할 수 있습니다. Mumu는 검증 가능한 플랫폼 결과가 있을 때만 완료로 표시하며, 모델의 자체 성공 주장, 스크린샷 존재 또는 부분 완료를 완료 증거로 사용하지 않습니다.

## 지원 플랫폼

| 플랫폼 | 과제 | 온라인 강의 | 출석 | 현재 지원 |
| :---: | :---: | :---: | :---: | --- |
| <img src="assets/platforms/xuexitong.png" height="28" alt="Xuexitong 로고"><br>**Xuexitong** | 지원 | 지원 | 지원 | 구조화 문제, 챕터 학습, 일괄 출석 및 공유 |
| <img src="assets/platforms/educoder.png" height="28" alt="EduCoder 로고"><br>**EduCoder** | 지원 | — | — | 클래스 실습, Java/JUnit 편집기, 자동 평가 및 제한적 복구 |
| <img src="assets/platforms/zhihuishu.ico" height="28" alt="Zhihuishu 로고"><br>**Zhihuishu** | — | 지원 | — | 강좌 작업 지점, 미디어 및 읽기 흐름 |
| <img src="assets/platforms/icourse.ico" height="28" alt="ICourse163 로고"><br>**ICourse163** | — | 지원 | — | 강좌 작업 지점, 미디어 및 읽기 흐름 |
| <img src="assets/platforms/icve.ico" height="28" alt="ICVE 로고"><br>**ICVE** | — | 지원 | — | 강좌 작업 지점, 미디어 및 읽기 흐름 |
| <img src="assets/platforms/zjy.ico" height="28" alt="ZJY 로고"><br>**ZJY** | — | 지원 | — | 강좌 작업 지점, 미디어 및 읽기 흐름 |
| <img src="assets/platforms/yuketang.ico" height="28" alt="Yuketang 로고"><br>**Yuketang** | — | 지원 | — | 강좌 작업 지점, 미디어 및 읽기 흐름 |

## Star History

<div align="center">

<a href="https://star-history.com/#ravicc02/mumu&amp;Date">
  <img src="https://api.star-history.com/svg?repos=ravicc02/mumu&amp;type=Date" width="100%" alt="Mumu Star History">
</a>

</div>

## 시작하기

1. [Releases](https://github.com/ravicc02/Mumu/releases)에서 Windows x64 설치 파일을 다운로드합니다. 릴리스 후보는 Pre-release로 표시됩니다.
2. 최초 실행 시 환경 검사를 확인합니다. 누락된 종속성을 설치할 수 있으면 설치하고, 실패하면 표시되는 수동 명령을 사용합니다.
3. 모델 엔드포인트, API Key, 모델 및 추론 강도를 설정한 뒤 플랫폼 계정을 인증합니다. 두 단계 모두 건너뛰고 나중에 설정에서 완료할 수 있습니다.
4. 작업을 선택해 실행합니다. Mumu가 수동 개입을 요청하면 원래 작업 브라우저에서 페이지를 확인한 뒤 계속합니다.
5. 플랫폼 검증 결과를 확인합니다. Xuexitong 최종 제출과 EduCoder 자동 평가는 과제 설정에서 각각 관리합니다.

**소스에서 실행**

지원 환경: Windows 10/11 x64, Node.js 24, Python 3.13, Chrome 또는 Edge.

~~~powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
npm --prefix desktop ci
$env:MUMU_PYTHON = (Resolve-Path .\.venv\Scripts\python.exe).Path
npm start
~~~

웹 앱을 미리 보려면 두 개의 터미널을 사용합니다.

~~~powershell
# 터미널 1: 저장소 루트
.\.venv\Scripts\python.exe backend\run_server.py --host 127.0.0.1 --port 8877

# 터미널 2: 저장소 루트
npm run desktop:web
~~~

프론트엔드는 http://127.0.0.1:5174/, 로컬 API는 http://127.0.0.1:8877/에서 실행됩니다. 개발 및 빌드 절차는 [CONTRIBUTING.md](CONTRIBUTING.md)를 참고하세요.

## 데이터 및 개인정보

기본 데이터 디렉터리는 계속 %LOCALAPPDATA%/AIAssistantStudio이며 인증, 작업, SQLite 데이터 및 스크린샷을 저장합니다. 기존 디렉터리 이름은 업그레이드 호환성을 위해 유지되며 제품 표시 이름이 바뀌어도 변경되지 않습니다. 모델 요청은 질문과 필요한 컨텍스트를 사용자가 설정한 모델 서비스로 전송합니다.

~~~text
logs/
  main.log       오늘의 상세 로그
  oldmain.log    보관된 이전 로그
~~~

문제가 발생하면 설정에서 로그 디렉터리를 열고 민감 정보가 제거된 main.log를 제공하세요. 공개 Issue에는 Cookie, API Key, QR 자격 증명 또는 인증 파일을 포함하면 안 됩니다. 자세한 내용은 [보안 및 개인정보](.github/SECURITY.md)를 참고하세요.

## 프로젝트 구조

~~~text
desktop/       Vue와 Electron; src/app은 진입점, src/features는 비즈니스 영역, src/shared는 공용 컨트롤
backend/       FastAPI, 플랫폼 어댑터 및 작업 실행; services는 계정, 과제, 작업, 런타임 및 알림 영역으로 구분
scripts/       버전 관리, 소스 감사, 패키징, 스모크 테스트 및 릴리스 도구
assets/        README에서 사용하는 브랜드, 데모 및 플랫폼 표시
.github/       CI, Release, Issue 템플릿 및 종속성 유지 관리
~~~

## 기여

[기여 안내](CONTRIBUTING.md) · [변경 기록](CHANGELOG.md)
