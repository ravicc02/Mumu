# 版本与发布流程

## 1. 版本来源

根目录 VERSION 是产品版本的唯一来源。文件内使用 X.Y.Z 或 X.Y.Z-rc.N，不带 v，不使用构建元数据后缀。Git 标签使用 vX.Y.Z 或 vX.Y.Z-rc.N。

| 位置 | 取值方式 |
| --- | --- |
| 根包、桌面包、锁文件 | scripts/version.mjs 同步并校验 |
| 前端“关于” | 读取同步后的桌面包元数据 |
| 后端健康检查、OpenAPI、连接器、日志环境 | backend/app/version.py 读取 VERSION |
| 冻结后端 | 打包携带 app/VERSION，缺失时明确报错 |
| 桌面 EXE、安装包 | electron-builder 读取桌面包版本；Windows 数值资源使用 X.Y.Z.0，显示字符串保留完整版本 |
| 构建与发行证据 | 记录版本、Git 提交、源文件摘要和产物 SHA-256 |

第三方包、平台协议和旧 Chrome 扩展版本独立维护，版本同步工具不修改它们。

~~~powershell
npm run version:check
npm run audit:source
npm run version:set -- 0.5.0
# 如果只编辑了 VERSION，也可执行：
npm run version:sync
~~~

当前仍处于 0.x 开发期：修复使用补丁版本，新增业务和明显交互变化使用次版本；破坏性接口或数据变化必须在更新日志中明确说明。预发布使用 rc.1、rc.2 等标识，不能替代正式版的兼容性验收。

## 2. 更新日志

日常变化进入 CHANGELOG.md 的 Unreleased。类别使用 Added、Changed、Fixed、Removed、Security，以及必要的 Known Limitations。记录用户可感知的变化及迁移事项，不复制提交列表或写未经核验的成功结论。

准备发行时，保留 Unreleased 标题，将本次变化移动到对应日期版本节，例如：

~~~markdown
## [Unreleased]

## [0.5.0] - 2026-11-13

### Added

- 本次实际交付的功能。

### Fixed

- 已修复的问题及改变后的行为。
~~~

上面仅为格式示例，不表示该版本已经发布。日期使用实际发行日期；平台故障、未实测账号和实验性功能放入已知限制。软件版本较早但尚无 Release 时，不补造历史发布记录。

## 3. 本地与 CI 检查

~~~powershell
npm run version:check
npm run release:check
npm test
npm run test:desktop
npm run test:backend
npm --prefix desktop run build:web
~~~

test:backend 使用当前环境的 python，请先激活项目虚拟环境。CI 固定 Node.js 24、Python 3.13，在 Windows 全新环境运行；浏览器测试明确指定隔离的 Playwright Chromium。

普通 release:check 检查版本一致性、文档链接、预览资源和第三方声明，可在脏工作区使用。严格检查额外要求：

- 干净且完整的 Git 检出，所有待交付源代码及第三方文件已提交。
- 当前版本在 CHANGELOG 中有带日期、内容完整的版本节。
- 当前提交已进入 origin/main。
- 指定标签与 VERSION 一致，并指向当前提交。
- Git 索引没有 .env、授权存储、数据库或日志等运行数据。

~~~powershell
npm run release:check -- --release --tag v0.5.0
~~~

严格检查不通过时，停止发行并修正原因；不跳过检查或覆盖旧版本。

## 4. 构建和产物验收

可在开发环境构建本地安装包，但正式发行必须从已经通过严格检查的干净检出构建。不要用开发机历史 dist 或 release 目录中的旧文件替代本次产物。

~~~powershell
npm --prefix desktop ci
npm --prefix desktop run build:win
powershell -ExecutionPolicy Bypass -File scripts/smoke-package.ps1
npm run release:notes -- --out desktop/release/RELEASE_NOTES.md
python -m pip freeze | Out-File desktop/release/build-environment.txt -Encoding utf8
npm run release:manifest
npm run release:verify
~~~

build:win 固定构建 Windows x64 NSIS，禁止 electron-builder 自动上传。构建过程生成来源指纹并将 VERSION、诊断身份及第三方许可一起放入后端。

冒烟脚本在独立临时数据目录和 18877 端口启动内置后端，检查健康响应和版本，并检查桌面 EXE 的 ProductVersion。端口被占用时直接失败，不结束其他进程。脚本退出时只结束自己启动的进程。

Release 工作流随后在全新 Windows runner 中静默安装 NSIS 安装包，实际启动已安装的 Electron 应用，验证原生依赖检测、首次初始化、跳过模型与授权、重启持久化及品牌图片加载。该验收只使用临时数据目录，记录进入 smoke-result.json；缺少安装验收记录时禁止生成发行清单。安装验收使用 8877 端口，预先发现占用时直接失败。

正式发行附件：

~~~text
Assignment-Studio-X.Y.Z-windows-x64-setup.exe
RELEASE_NOTES.md
release-manifest.json
SHA256SUMS.txt
build-environment.txt
smoke-result.json
~~~

release-manifest.json 包含提交、版本、构建指纹、附件大小及摘要。SHA256SUMS.txt 覆盖安装包、发行说明、构建环境、冒烟结果及清单本身。生成清单时，会核对安装包内后端的版本、提交和源文件指纹，拒绝复用不匹配的后端。

当前尚未配置 Windows 代码签名。构建成功、摘要一致和已签名属于不同结论，不在发行说明中混用。

## 5. 标签与 GitHub Release

工作流分为 CI 和 Release Draft：前者检查 main 提交及 Pull Request，后者由版本标签触发。官方 Actions 固定到提交摘要，升级时同时更新注释中的版本及摘要。

1. 完成版本同步、更新日志、测试及审查，将完整修改合入 main。
2. 确认 main 已推送，获取最新 main 和标签。
3. 创建带说明的版本标签，运行严格检查，再推送该标签。
4. Release Draft 工作流重新测试、构建、冒烟和生成证据，创建 GitHub Release 草稿。
5. 在干净机器安装草稿安装包，核对版本、启动、授权窗口、配置保存及日志路径。真实业务测试须单独指定账号及范围。
6. 检查附件、校验和、已知限制及第三方声明后，再将草稿发布。

~~~powershell
# 以下以已经合入 main 的 0.5.0 为例；实际版本以 VERSION 为准。
git fetch origin main --tags
git tag -a v0.5.0 -m "Assignment Studio 0.5.0"
npm run release:check -- --release --tag v0.5.0
git push origin v0.5.0
~~~

普通代码修改不会触发 Release。工作流只创建草稿；不会替维护者完成干净机器安装验收或正式发布。已发布标签、附件和版本不能覆盖，发现问题后发行新补丁版本。

### 手动重跑与发布

Release 工作流也提供手动入口，填写已经存在的版本标签。默认只生成草稿；勾选 publish 时，重新测试、构建和冒烟后，下载草稿附件并校验来源、版本及全部 SHA-256，再发布。依赖安装步骤不需要个人账号凭据。

~~~powershell
gh workflow run release.yml -f tag=v0.5.0-rc.6 -f publish=false
~~~

同一标签允许重跑并更新草稿附件。目标版本已经公开发布时，工作流立即拒绝覆盖。网络和打包失败可在修复后重跑；需要变更已经发布的源代码时创建新版本。

已有通过流水线的草稿时，也可以从对应标签的干净检出发布：

~~~powershell
npm run release:publish -- v0.5.0-rc.6
~~~

该命令使用 gh 的本机登录，重新下载六项附件，核对校验和、清单和 Git 提交后发布。包含连字符的版本标为预发布，不设为最新稳定版。用户更新检查继续只跟踪正式版本。

rc 标签创建预发布草稿。稳定更新检查仅跟踪正式 Release；同版本正式版可替代本机 rc。当前客户端检查更新后打开发行页，不执行自动下载或安装。

## 6. 数据迁移与回退

数据库变更必须兼容现有数据，并通过迁移测试。升级前停止业务任务并保留数据目录副本；正式记录迁移要求。旧安装包未必能读取新版数据库，回退前确认兼容性或使用升级前备份，不能直接删除用户数据。

发行问题保留失败安装包的摘要、对应提交及日志。用户当天问题主要收集 main.log，历史问题按需收集 oldmain.log；不要要求用户提交 Cookie、API Key、Profile 或数据库到公开 Issue。
