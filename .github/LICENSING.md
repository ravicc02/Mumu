# 许可证与第三方声明

## 当前授权状态

Mumu 自身代码采用 GNU General Public License version 3 only，SPDX 标识为 `GPL-3.0-only`。

源码可以复制、修改和再发布，也允许收费和商业使用。分发修改版或目标代码时，必须同时满足 GPL 的源码提供、许可证通知和同许可证授权要求。不能把 Mumu 的修改版封装成闭源软件再分发。

许可证全文位于根目录 `LICENSE`。许可证名称、本说明和 README 都不能替代正式全文。

## 可以做什么

- 运行未修改的 Mumu。
- 为个人或商业目的使用 Mumu。
- 修改源码并用私人修改版执行自己的任务。
- 在遵守 GPL 的条件下复制、分发或收费提供 Mumu。
- 提供付费部署、配置、支持和培训服务。

GPL 允许收费，但不允许通过额外限制阻止接收者继续复制、修改或分发其取得的 GPL 软件。

## 分发要求

向他人提供 Mumu 源码或目标代码时，至少要注意以下要求：

- 随软件提供 GPL-3.0 许可证文本。
- 保留版权、许可证和无担保声明。
- 修改版需要标明修改事实和修改日期。
- 分发目标代码时，以 GPL 允许的方式提供 Corresponding Source（对应源码）。
- 修改版整体继续按 GPL-3.0 授权接收者。
- 用户产品需要安装信息时，提供重新安装修改版所需的必要信息。

通过网络让用户操作本机 Mumu，并不自动等同于分发副本。是否需要提供源码，取决于是否向他人 conveyed（传递）了副本或目标代码。

## 文件位置

| 文件 | 用途 |
| --- | --- |
| `LICENSE` | GPL-3.0-only 完整许可证文本 |
| `LICENSES.txt` | 仓库内集中保存第三方许可证全文及版权声明 |
| `desktop/public/licenses/OPEN_SOURCE_LICENSES.txt` | 安装包内携带的同一组第三方许可证文本 |
| `package.json` | 根包许可证元数据 |
| `desktop/package.json` | 桌面端许可证元数据 |

两份第三方许可证文件必须内容一致。`npm run release:check` 会拒绝缺失、路径错误或内容不一致的发行输入。

第三方许可证文本与 Mumu 自身许可证分开保存。把所有许可证合并成一段文本，会掩盖不同组件之间的义务差异。

## 第三方代码

新增依赖、复制代码或迁移实现前，维护者需要完成以下检查：

1. 确认组件版本、实际许可证和对应源码位置。
2. 核对第三方许可证与 GPL-3.0-only 是否兼容。
3. 保留许可证要求的版权声明、许可文本和必要通知。
4. 必要时补充源码获取方式、可替换方式或安装信息。
5. 将新增文本同步到两份第三方许可证文件。
6. 运行 `npm run release:check` 和相关构建测试。

第三方组件仍按其原许可证执行。GPL 不能替代 MIT、Apache、LGPL 或 Commons Clause 等第三方义务。

当前集中清单包含带 Commons Clause 条件的条目。该条件可能对再分发施加额外限制。在以 GPL-3.0-only 发布安装包前，维护者必须确认相关代码是否属于覆盖作品，并完成移除、替换、隔离或许可审查；未完成审查时不得宣称第三方许可证已经兼容。

## 与 LGPL-3.0 的区别

LGPL-3.0 也允许商业使用，但允许在满足特定条件下把库与不同许可证的应用程序组合。

GPL-3.0-only 的 copyleft 要求更强。修改和分发 Mumu 时，整个覆盖作品需要按 GPL 授权接收者。需要允许闭源应用以较少义务链接本项目时，才考虑 LGPL。

当前选择是 GPL-3.0-only，不使用 LGPL，也不额外附加禁止商业使用的条款。

## English Summary

Mumu source code is licensed under `GPL-3.0-only`. Commercial use and charging for copies are permitted. Distribution of modified source or object code must comply with GPL-3.0, including corresponding source, notices, and licensing the covered work under the same license.

The project cannot be distributed as a closed-source derivative. The full license is in the root `LICENSE` file.

Third-party license texts and notices are centralized in `LICENSES.txt` and `desktop/public/licenses/OPEN_SOURCE_LICENSES.txt`. Both files must remain identical and are checked during release validation.
