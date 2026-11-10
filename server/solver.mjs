const VALID_TYPES = new Set([
  "single_choice",
  "multiple_choice",
  "text",
  "unknown"
]);

function cleanString(value, maxLength = 4000) {
  if (typeof value !== "string") {
    return "";
  }
  return value.replace(/\u0000/g, "").trim().slice(0, maxLength);
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

export function normalizeQuestion(input) {
  if (!input || typeof input !== "object") {
    throw new Error("题目数据不能为空");
  }

  const type = VALID_TYPES.has(input.type) ? input.type : "unknown";
  const stem = cleanString(input.stem, 12000);
  if (!stem) {
    throw new Error("没有识别到题干");
  }

  const options = Array.isArray(input.options)
    ? input.options.slice(0, 50).map((option, index) => ({
        index: Number.isInteger(option?.index) ? option.index : index,
        text: cleanString(option?.text, 2000),
        value: cleanString(option?.value, 500)
      })).filter((option) => option.text)
    : [];

  return {
    type,
    stem,
    options,
    pageTitle: cleanString(input.pageTitle, 500),
    pageUrl: cleanString(input.pageUrl, 2000)
  };
}

export function extractJsonObject(text) {
  const content = cleanString(text, 30000);
  if (!content) {
    throw new Error("AI 返回为空");
  }

  try {
    return JSON.parse(content);
  } catch {
    // Continue with brace extraction for fenced or mixed responses.
  }

  const start = content.indexOf("{");
  if (start === -1) {
    throw new Error("AI 返回中没有 JSON 对象");
  }

  let depth = 0;
  let inString = false;
  let escaped = false;

  for (let index = start; index < content.length; index += 1) {
    const character = content[index];

    if (inString) {
      if (escaped) {
        escaped = false;
      } else if (character === "\\") {
        escaped = true;
      } else if (character === "\"") {
        inString = false;
      }
      continue;
    }

    if (character === "\"") {
      inString = true;
      continue;
    }

    if (character === "{") {
      depth += 1;
    } else if (character === "}") {
      depth -= 1;
      if (depth === 0) {
        const candidate = content.slice(start, index + 1);
        return JSON.parse(candidate);
      }
    }
  }

  throw new Error("AI 返回的 JSON 不完整");
}

export function normalizeAnswer(rawAnswer, question) {
  if (!rawAnswer || typeof rawAnswer !== "object") {
    throw new Error("AI 没有返回有效答案");
  }

  const candidate = rawAnswer.answer && typeof rawAnswer.answer === "object"
    ? rawAnswer.answer
    : rawAnswer;
  const type = VALID_TYPES.has(candidate.type) ? candidate.type : question.type;
  const selectedIndices = Array.isArray(candidate.selectedIndices)
    ? candidate.selectedIndices
        .map((value) => Number(value))
        .filter(Number.isInteger)
        .filter((value) => question.options.some((option) => option.index === value))
    : [];
  const uniqueIndices = [...new Set(selectedIndices)];
  const textAnswer = cleanString(candidate.textAnswer, 12000);
  const confidenceValue = Number(candidate.confidence);
  const confidence = Number.isFinite(confidenceValue)
    ? clamp(confidenceValue, 0, 1)
    : 0.5;

  const normalized = {
    type,
    selectedIndices: type === "single_choice"
      ? uniqueIndices.slice(0, 1)
      : uniqueIndices,
    textAnswer,
    confidence,
    explanation: cleanString(candidate.explanation, 4000)
  };

  if (
    (normalized.type === "single_choice" || normalized.type === "multiple_choice")
    && normalized.selectedIndices.length === 0
  ) {
    throw new Error("AI 没有返回有效的选项索引");
  }

  if (normalized.type === "text" && !normalized.textAnswer) {
    throw new Error("AI 没有返回文本答案");
  }

  return normalized;
}

function buildSystemPrompt() {
  return [
    "你是一个谨慎的答题辅助程序。",
    "只依据用户提供的题干和选项作答，不要假设页面中不可见的信息。",
    "必须只输出一个 JSON 对象，不要输出 Markdown 代码块或额外说明。",
    "JSON 字段必须包含：type、selectedIndices、textAnswer、confidence、explanation。",
    "type 只能是 single_choice、multiple_choice、text、unknown。",
    "单选题 selectedIndices 必须恰好包含一个从 0 开始的选项索引。",
    "多选题 selectedIndices 可以包含多个从 0 开始的选项索引。",
    "文本题使用 textAnswer，选择题将 textAnswer 设为空字符串。",
    "confidence 使用 0 到 1 之间的小数。信息不足时降低置信度，不要编造。",
    "explanation 使用简短中文说明判断依据。"
  ].join("\n");
}

function buildUserPrompt(question) {
  return JSON.stringify({
    task: "回答下面的题目",
    type: question.type,
    stem: question.stem,
    options: question.options.map((option) => ({
      index: option.index,
      text: option.text
    })),
    answerSchema: {
      type: question.type,
      selectedIndices: [],
      textAnswer: "",
      confidence: 0.8,
      explanation: ""
    }
  }, null, 2);
}

function readMessageContent(payload) {
  const content = payload?.choices?.[0]?.message?.content;
  if (typeof content === "string") {
    return content;
  }
  if (Array.isArray(content)) {
    return content
      .map((part) => typeof part?.text === "string" ? part.text : "")
      .join("");
  }
  throw new Error("AI 响应格式不支持");
}

function createMockAnswer(question) {
  if (question.type === "text") {
    return {
      type: "text",
      selectedIndices: [],
      textAnswer: "这是本地演示答案，请配置真实 AI 服务后替换。",
      confidence: 0.1,
      explanation: "当前为本地演示模式，不代表正确答案。"
    };
  }

  return {
    type: question.type === "unknown" ? "single_choice" : question.type,
    selectedIndices: question.options.length > 0 ? [question.options[0].index] : [],
    textAnswer: "",
    confidence: 0.1,
    explanation: "当前为本地演示模式，不代表正确答案。"
  };
}

export async function solveQuestion(question, config, fetchImpl = fetch) {
  if (config.mockMode) {
    return {
      ...normalizeAnswer(createMockAnswer(question), question),
      model: "local-demo",
      mode: "mock"
    };
  }

  const endpoint = `${config.baseUrl.replace(/\/+$/, "")}/chat/completions`;
  const requestBody = {
    model: config.model,
    temperature: 0.2,
    max_tokens: 1600,
    messages: [
      { role: "system", content: buildSystemPrompt() },
      { role: "user", content: buildUserPrompt(question) }
    ]
  };

  if (config.jsonMode) {
    requestBody.response_format = { type: "json_object" };
  }
  if (config.reasoningEffort) {
    requestBody.reasoning_effort = config.reasoningEffort;
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), config.timeoutMs);

  let response;
  try {
    response = await fetchImpl(endpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${config.apiKey}`
      },
      body: JSON.stringify(requestBody),
      signal: controller.signal
    });
  } catch (error) {
    if (error?.name === "AbortError") {
      throw new Error("AI 请求超时");
    }
    throw new Error(`无法连接 AI 服务：${error.message}`);
  } finally {
    clearTimeout(timer);
  }

  if (!response.ok) {
    const details = cleanString(await response.text(), 1000);
    throw new Error(`AI 服务返回 ${response.status}${details ? `：${details}` : ""}`);
  }

  const payload = await response.json();
  const content = readMessageContent(payload);
  const parsed = extractJsonObject(content);
  return {
    ...normalizeAnswer(parsed, question),
    model: config.model,
    reasoningEffort: config.reasoningEffort || "",
    mode: "live"
  };
}

export const solverInternals = {
  buildSystemPrompt,
  buildUserPrompt,
  createMockAnswer
};
