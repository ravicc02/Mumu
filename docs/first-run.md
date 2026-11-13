# 首次初始化

沐沐使用现有玻璃水球角色作为应用形象。首次运行展示沐沐与不同形状的任务伙伴，随后进入三步初始化。只维护 Windows PC 体验。

## 环境检测

| 检测项 | 安装包 | 源码开发环境 |
| --- | --- | --- |
| 运行环境 | 检查内置后端文件；Python 和业务库由安装包提供 | 使用指定 Python 检查运行库，缺少时安装 requirements.txt |
| Chrome / Edge | 检查已有浏览器，缺少时可安装 Chrome | 与安装包相同 |
| 本地数据目录 | 检查目录是否可写 | 与安装包相同 |
| 服务连接 | 依赖就绪后启动本地后端，等待健康检查 | 可复用已启动的本地服务 |

“重新检测”只进行检测。“安装缺少的依赖”在用户点击后运行固定安装命令，完成后再次检测。检测或安装失败会保留原因与手动命令，不把安装进程退出当作环境已就绪。

安装包内置文件缺失时，应重新安装应用，不通过 pip 修改已经打包的后端。安装包用户不需要安装 Node.js 或 Python。源码开发推荐 Python 3.13；路径可通过 ANSWER_STUDIO_PYTHON 指定。

Chrome 的手动安装命令：

~~~powershell
winget install --id Google.Chrome --exact --accept-package-agreements --accept-source-agreements
~~~

源码环境的运行库安装命令：

~~~powershell
python -m pip install -r backend\requirements.txt
~~~

缺少 Python 时，初始化支持先安装 Python 3.13，再安装运行库。WinGet 不可用、网络受限或权限不足时，界面显示失败原因。安装完成后需重新检测。命令参数说明见 [Microsoft WinGet 安装文档](https://learn.microsoft.com/windows/package-manager/winget/install)。

环境检测必须通过，才能进入后续步骤。正在运行旧版后端的网页预览需在任务结束后重启服务，才能使用新检测接口。

## 模型配置

复用设置中的模型配置组件，包含接口地址、密钥、模型顺序、思考强度、模型列表获取及连接测试。保存后才能按“下一步”；也可以选择“稍后配置”。密钥由既有后端配置接口保存，不写入初始化完成标记。

跳过只代表未完成配置，不代表模型可用。之后可从左下角设置补充。

## 平台授权

平台列表来自统一能力目录，展示学习通、头歌、知到智慧树、中国大学 MOOC、智慧职教、职教云和雨课堂，以及各自支持的业务。

选择平台后点击授权，在独立登录窗口完成登录。复用现有授权流程获取平台身份、名称和头像。同一平台的稳定用户 ID 用于去重。授权结果会显示在当前步骤，也可以跳过，进入平台页后再添加。

## 完成与重新进入

初始化完成标记按本地数据目录保存，包含完成时间以及模型、平台是否跳过，不包含登录态、模型密钥或题目。已有已授权账号的工作区自动兼容，不要求重新授权。

“设置 → 关于 → 重新初始化”可以再次进入。重新进入不会删除模型配置、已有账号或任务数据。

## 品牌素材维护

| 文件 | 用途 |
| --- | --- |
| desktop/public/brand/mumu.png | 顶栏、关于页、初始化页使用的纯沐沐形象 |
| desktop/build/icon.png、icon.ico | Electron 窗口及 Windows 应用图标 |
| docs/assets/mumu-bots.gif | README 的 8 秒循环品牌动画 |
| docs/assets/mumu-bots.png | 静态预览 |
| scripts/render-brand.mjs | 从现有角色代码导出图标、静态图和动画 |

品牌画面直接复用项目角色，不依赖远程图片或第三方素材服务。动画支持减少动效，页面隐藏时暂停渲染；WebGL 不可用时显示纯沐沐图片。

导出工具需要已启动的本地 Vite、Playwright、Chrome 和 FFmpeg。工具依赖只用于维护素材，不是使用安装包的前置要求。BRAND_NODE_MODULES 可指定已有 Playwright 包目录；BRAND_PREVIEW_URL、BRAND_CHROME 和 BRAND_FFMPEG 可覆盖本地工具位置。

~~~powershell
node scripts/render-brand.mjs
~~~

初始化界面的隔离回归可运行 node scripts/test-startup-ui.mjs。该脚本使用演示响应，验证跳过、模型保存、授权结果、持久化、依赖门禁与动画非空像素，不调用真实平台授权或安装程序。
