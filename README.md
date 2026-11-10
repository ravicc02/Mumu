# AI Quiz Assistant

一个用于自有或获授权页面的 Chrome 侧边栏答题助手。扩展复用当前浏览器登录状态，不读取或上传 Cookie；本地服务负责调用 AI。

## 当前功能

- 扫描当前页面题目，支持单选、多选和文本题
- 调用 OpenAI-compatible `/chat/completions` 接口
- 生成答案后先预览，点击后才填入页面
- 支持按站点保存 CSS 选择器规则
- 未配置 API Key 时进入明确标注的演示模式
- 不自动提交，不处理或绕过验证码

## 启动

```powershell
cd E:\Desktop\workshop\ai-quiz-assistant
npm start
```

服务默认运行在 `http://127.0.0.1:8787`，示例页面：

```text
http://127.0.0.1:8787/demo/quiz.html
```

## 加载扩展

1. 打开 Chrome 的 `chrome://extensions/`。
2. 开启右上角“开发者模式”。
3. 点击“加载已解压的扩展程序”。
4. 选择 `E:\Desktop\workshop\ai-quiz-assistant\extension`。
5. 打开示例页面，点击工具栏中的扩展图标。
6. 在侧边栏依次点击“授权并扫描”、“获取答案”、“填入答案”，核对后手动点击页面上的“下一题”。

## 配置真实 AI

在项目根目录创建 `.env`，字段参考 `.env.example`：

```dotenv
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=your-key
OPENAI_MODEL=gpt-4.1-mini
AI_REASONING_EFFORT=medium
OPENAI_JSON_MODE=false
AI_MOCK=false
```

实际配置文件位于项目根目录 `.env`。`OPENAI_BASE_URL` 填 API 根地址，本地服务会自动拼接 `/chat/completions`。`AI_REASONING_EFFORT` 可填 `low`、`medium`、`high`，留空则不发送该参数。修改后重启 `npm start`。如果服务商不支持 `response_format={"type":"json_object"}`，保持 `OPENAI_JSON_MODE=false`。

## 接入其他网站

侧边栏的“站点规则”可以按域名保存以下选择器：

- 题目容器，例如 `[data-question]`
- 题干，例如 `.question-title`
- 选项，例如 `label.option`
- 下一题按钮，例如 `#next`

选择器留空时，扩展会尝试使用内置的通用 DOM 识别。不同网站结构差异较大，稳定使用时通常需要为每个网站补一套选择器。

## 验证

```powershell
npm test
node --check extension/content.js
node --check extension/sidepanel.js
```

## 使用边界

仅用于自己拥有或明确获授权的页面、内部培训、表单和测试环境。自动答题可能违反考试、认证平台或第三方网站规则，提交前应人工核对，并遵守目标网站条款。
