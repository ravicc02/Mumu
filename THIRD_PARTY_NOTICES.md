# 第三方来源与许可

本文件提供来源索引，不替代各目录中的完整许可。主项目尚未声明整体许可证；第三方内容沿用各自的许可和声明。

| 内容 | 来源与固定记录 | 仓库内说明 |
| --- | --- | --- |
| 网课适配算法 | [ocsjs/ocsjs](https://github.com/ocsjs/ocsjs)，5.4.0，提交 1e93399a22b54fac80bb6bae131a4b208b5d8af9 | [适配说明](third_party/ocs/README.md)、[原始许可](third_party/ocs/upstream/LICENSE) |
| 吉祥物引擎 | [open-mascot-studio](https://github.com/0oooh/open-mascot-studio)，具体来源见本地 UPSTREAM.json | [原始许可](third_party/open-mascot/LICENSE)、[打包声明](desktop/public/licenses/open-mascot.txt) |
| LeiBox 交互适配 | [leishifu666/LeiBox](https://github.com/leishifu666/LeiBox)，提交 f7a4c8acf55bc1cf817d6804373ae224340f3357 | [来源记录](desktop/src/components/leibox/SOURCES.md)、[原始许可](desktop/src/components/leibox/LICENSE.md) |
| 二维码解码 | jsQR 1.4.0，Apache-2.0 | [后端随附许可](backend/app/services/attendance/JSQR-LICENSE) |
| 场景寻路 | [PathFinding.js](https://github.com/qiao/PathFinding.js) 0.4.18，MIT；依赖 heap 0.2.5，PSF | [PathFinding 声明](desktop/public/licenses/pathfinding.txt)、[heap 声明](desktop/public/licenses/heap.txt) |
| 平台标识 | 平台官方图标及已记录来源的标识 | [打包声明](desktop/public/third-party-notices.txt) |

Three.js、Cannon、Vue、Electron、Playwright、browser-use 等依赖通过各自的包管理元数据和包内许可追溯。版本清单保留在锁文件和构建环境记录中。

更新第三方代码时，保留原版权、固定来源提交及修改说明。OCS 生成运行文件通过 scripts/build-ocs-study.mjs 更新，同时保留 upstream-manifest.json 的来源摘要。不要手工修改生成文件后忽略来源记录。

发布前确认安装包携带 desktop/public/third-party-notices.txt、licenses 目录以及后端资源对应的许可证。平台 Logo 仅用于标识服务，不表示平台认可或合作关系。
