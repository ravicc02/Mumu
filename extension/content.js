(() => {
  if (globalThis.__AI_QUIZ_ASSISTANT_LOADED__) {
    return;
  }
  globalThis.__AI_QUIZ_ASSISTANT_LOADED__ = true;

  const QUESTION_CANDIDATE_SELECTOR = [
    "[data-aqa-question]",
    "[data-question]",
    "fieldset",
    "[role='group']",
    ".question",
    ".quiz-question",
    ".question-item",
    ".question-card",
    "[class*='question']",
    "[class*='quiz']"
  ].join(",");

  const STEM_SELECTORS = [
    "[data-question-stem]",
    ".question-stem",
    ".question-title",
    ".stem",
    ".title",
    "legend",
    "h1",
    "h2",
    "h3",
    "h4",
    "p"
  ];

  function cleanText(value) {
    return String(value ?? "")
      .replace(/\u00a0/g, " ")
      .replace(/\s+/g, " ")
      .trim();
  }

  function isElementVisible(element) {
    if (!(element instanceof Element)) {
      return false;
    }
    if (element.getAttribute("aria-hidden") === "true") {
      return false;
    }
    const style = getComputedStyle(element);
    if (
      style.display === "none"
      || style.visibility === "hidden"
      || Number(style.opacity) === 0
    ) {
      return false;
    }
    const rect = element.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }

  function queryAll(selector, root = document) {
    if (!selector) {
      return [];
    }
    try {
      return [...root.querySelectorAll(selector)];
    } catch {
      throw new Error(`选择器无效：${selector}`);
    }
  }

  function getLabelForInput(input) {
    if (input.labels?.length) {
      const visibleLabel = [...input.labels].find(isElementVisible);
      if (visibleLabel) {
        return visibleLabel;
      }
    }

    if (input.id) {
      try {
        const escapedId = globalThis.CSS?.escape
          ? CSS.escape(input.id)
          : input.id.replace(/["\\]/g, "\\$&");
        const explicitLabel = document.querySelector(`label[for="${escapedId}"]`);
        if (explicitLabel) {
          return explicitLabel;
        }
      } catch {
        // The closest label and nearby text are still usable fallbacks.
      }
    }

    return input.closest("label");
  }

  function isChoiceUsable(input) {
    if (input.disabled || input.type === "hidden") {
      return false;
    }
    const label = getLabelForInput(input);
    return isElementVisible(input) || Boolean(label && isElementVisible(label));
  }

  function getChoiceInputs(container) {
    return [...container.querySelectorAll("input[type='radio'], input[type='checkbox']")]
      .filter(isChoiceUsable);
  }

  function getTextControl(container) {
    const controls = [
      ...container.querySelectorAll("textarea"),
      ...container.querySelectorAll(
        "input[type='text'], input[type='search'], input[type='number'], input:not([type])"
      ),
      ...container.querySelectorAll("[contenteditable='true']")
    ];
    return controls.find(isElementVisible) || null;
  }

  function countControls(container) {
    return container.querySelectorAll(
      "input[type='radio'], input[type='checkbox'], textarea, input[type='text'], input[type='search'], [contenteditable='true']"
    ).length;
  }

  function scoreQuestionCandidate(element, customMatch) {
    if (!isElementVisible(element)) {
      return -1;
    }
    const controls = countControls(element);
    if (controls === 0 && !customMatch) {
      return -1;
    }

    const rect = element.getBoundingClientRect();
    const textLength = cleanText(element.innerText).length;
    let score = customMatch ? 100 : 0;

    score += controls > 0 ? 24 : 0;
    score += controls >= 2 && controls <= 16 ? 12 : 0;
    score += textLength >= 8 && textLength <= 3000 ? 10 : 0;
    score += rect.top >= -80 && rect.top < innerHeight * 0.85 ? 18 : 0;
    score -= Math.max(0, controls - 24);
    score -= Math.max(0, textLength - 6000) / 100;

    return score;
  }

  function findQuestionContainer(config) {
    if (config.questionSelector) {
      const configured = queryAll(config.questionSelector)
        .map((element, index) => ({
          element,
          index,
          score: scoreQuestionCandidate(element, true)
        }))
        .filter((item) => item.score >= 0)
        .sort((a, b) => b.score - a.score || a.index - b.index);
      if (configured.length > 0) {
        return configured[0].element;
      }
    }

    const candidates = [];
    const seen = new Set();

    for (const element of queryAll(QUESTION_CANDIDATE_SELECTOR)) {
      if (seen.has(element)) {
        continue;
      }
      seen.add(element);
      const score = scoreQuestionCandidate(element, false);
      if (score >= 0) {
        candidates.push({ element, index: candidates.length, score });
      }
    }

    if (candidates.length > 0) {
      candidates.sort((a, b) => b.score - a.score || a.index - b.index);
      return candidates[0].element;
    }

    const firstControl = [
      ...document.querySelectorAll(
        "input[type='radio'], input[type='checkbox'], textarea, [contenteditable='true']"
      )
    ].find(isElementVisible);

    let ancestor = firstControl?.parentElement || null;
    while (ancestor && ancestor !== document.body) {
      if (countControls(ancestor) > 0 && cleanText(ancestor.innerText).length >= 4) {
        return ancestor;
      }
      ancestor = ancestor.parentElement;
    }

    return null;
  }

  function extractStem(container, config) {
    if (config.stemSelector) {
      const configuredStem = queryAll(config.stemSelector, container)
        .find(isElementVisible);
      const configuredText = cleanText(configuredStem?.innerText);
      if (configuredText) {
        return configuredText.slice(0, 12000);
      }
    }

    for (const selector of STEM_SELECTORS) {
      const stems = queryAll(selector, container).filter(isElementVisible);
      for (const stem of stems) {
        const text = cleanText(stem.innerText);
        if (text.length >= 4 && text.length <= 12000) {
          return text;
        }
      }
    }

    const labelledBy = container.getAttribute("aria-labelledby");
    if (labelledBy) {
      const text = labelledBy
        .split(/\s+/)
        .map((id) => cleanText(document.getElementById(id)?.innerText))
        .filter(Boolean)
        .join(" ");
      if (text) {
        return text.slice(0, 12000);
      }
    }

    const clone = container.cloneNode(true);
    clone.querySelectorAll(
      "input, textarea, select, button, label, [role='radio'], [role='checkbox']"
    ).forEach((element) => element.remove());
    return cleanText(clone.textContent).slice(0, 12000);
  }

  function extractOptionRecords(container, config) {
    if (config.optionSelector) {
      const elements = queryAll(config.optionSelector, container)
        .filter((element) => isElementVisible(element) || Boolean(getLabelForInput(element)));
      return elements.map((element, index) => {
        const input = element.matches("input")
          ? element
          : element.querySelector("input[type='radio'], input[type='checkbox']")
            || (element.htmlFor ? document.getElementById(element.htmlFor) : null);
        return {
          index,
          text: cleanText(element.innerText || getLabelForInput(input)?.innerText),
          value: cleanText(input?.value),
          input,
          clickTarget: element
        };
      }).filter((option) => option.text);
    }

    const inputs = getChoiceInputs(container);
    return inputs.map((input, index) => {
      const label = getLabelForInput(input);
      return {
        index,
        text: cleanText(label?.innerText || input.getAttribute("aria-label") || input.value),
        value: cleanText(input.value),
        input,
        clickTarget: label || input
      };
    }).filter((option) => option.text);
  }

  function hashSignature(value) {
    let hash = 2166136261;
    for (let index = 0; index < value.length; index += 1) {
      hash ^= value.charCodeAt(index);
      hash = Math.imul(hash, 16777619);
    }
    return (hash >>> 0).toString(16).padStart(8, "0");
  }

  function detectQuestionType(container, config, options, textControl) {
    if (["single_choice", "multiple_choice", "text"].includes(config.type)) {
      return config.type;
    }
    const hasCheckbox = [...container.querySelectorAll("input[type='checkbox']")]
      .some(isChoiceUsable);
    if (hasCheckbox) {
      return "multiple_choice";
    }
    if (options.length > 0) {
      return "single_choice";
    }
    if (textControl) {
      return "text";
    }
    return "unknown";
  }

  function captureQuestion(config = {}) {
    const container = findQuestionContainer(config);
    if (!container) {
      throw new Error("没有找到题目容器，请在站点规则中填写题目选择器");
    }

    const options = extractOptionRecords(container, config);
    const textControl = getTextControl(container);
    const stem = extractStem(container, config);
    if (!stem) {
      throw new Error("找到题目容器，但没有识别到题干");
    }

    const type = detectQuestionType(container, config, options, textControl);
    const signatureSource = [
      location.href,
      type,
      stem,
      ...options.map((option) => option.text)
    ].join("\n");
    const question = {
      signature: hashSignature(signatureSource),
      type,
      stem,
      options: options.map(({ index, text, value }) => ({ index, text, value })),
      textEntry: textControl
        ? {
            placeholder: cleanText(
              textControl.getAttribute("placeholder")
              || textControl.getAttribute("aria-label")
            )
          }
        : null,
      pageTitle: document.title,
      pageUrl: location.href
    };

    container.scrollIntoView({ block: "center", behavior: "smooth" });
    return question;
  }

  function setNativeValue(element, value) {
    if (element instanceof HTMLTextAreaElement || element instanceof HTMLInputElement) {
      const prototype = element instanceof HTMLTextAreaElement
        ? HTMLTextAreaElement.prototype
        : HTMLInputElement.prototype;
      const descriptor = Object.getOwnPropertyDescriptor(prototype, "value");
      descriptor?.set?.call(element, value);
    } else {
      element.textContent = value;
    }

    try {
      element.dispatchEvent(new InputEvent("input", {
        bubbles: true,
        inputType: "insertText",
        data: value
      }));
    } catch {
      element.dispatchEvent(new Event("input", { bubbles: true }));
    }
    element.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function sleep(milliseconds) {
    return new Promise((resolve) => setTimeout(resolve, milliseconds));
  }

  async function applyAnswer(question, answer, config = {}) {
    const currentQuestion = captureQuestion(config);
    if (currentQuestion.signature !== question.signature) {
      throw new Error("页面题目已经变化，请重新扫描");
    }

    if (answer.type === "text") {
      const container = findQuestionContainer(config);
      const control = getTextControl(container);
      if (!control) {
        throw new Error("没有找到文本输入框");
      }
      control.focus();
      setNativeValue(control, answer.textAnswer || "");
      control.blur();
      return { applied: true, kind: "text" };
    }

    const container = findQuestionContainer(config);
    const optionRecords = extractOptionRecords(container, config);
    const selected = new Set(answer.selectedIndices || []);
    if ([...selected].some((index) => !optionRecords.some((option) => option.index === index))) {
      throw new Error("答案索引与当前页面选项不匹配");
    }

    for (const option of optionRecords) {
      const shouldSelect = selected.has(option.index);
      if (!option.input) {
        if (shouldSelect) {
          option.clickTarget.click();
        }
        continue;
      }

      const isCheckbox = option.input.type === "checkbox";
      const needsClick = isCheckbox
        ? option.input.checked !== shouldSelect
        : shouldSelect && !option.input.checked;

      if (needsClick) {
        option.clickTarget.click();
        await sleep(20);
        if (shouldSelect && !option.input.checked) {
          option.input.click();
        }
      }
    }

    return {
      applied: true,
      kind: answer.type,
      selectedIndices: [...selected]
    };
  }

  function clickNext(config = {}) {
    let target = null;
    if (config.nextSelector) {
      target = queryAll(config.nextSelector)
        .find((element) => isElementVisible(element) && !element.disabled);
    }

    if (!target) {
      const buttons = [...document.querySelectorAll("button, a, [role='button']")]
        .filter((element) => isElementVisible(element) && !element.disabled);
      target = buttons.find((element) => /^(下一题|下一页|继续|next)$/i.test(cleanText(element.innerText)));
    }

    if (!target) {
      throw new Error("没有找到下一题按钮，请填写下一题选择器");
    }

    target.scrollIntoView({ block: "center", behavior: "smooth" });
    target.click();
    return { clicked: true, text: cleanText(target.innerText) };
  }

  globalThis.__AI_QUIZ_ASSISTANT_INTERNALS__ = {
    captureQuestion,
    applyAnswer,
    clickNext,
    findQuestionContainer
  };

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (!message || typeof message.type !== "string") {
      return false;
    }

    Promise.resolve()
      .then(async () => {
        if (message.type === "PING") {
          return { ok: true };
        }
        if (message.type === "CAPTURE_QUESTION") {
          return { ok: true, question: captureQuestion(message.config) };
        }
        if (message.type === "APPLY_ANSWER") {
          return {
            ok: true,
            result: await applyAnswer(message.question, message.answer, message.config)
          };
        }
        if (message.type === "CLICK_NEXT") {
          return { ok: true, result: clickNext(message.config) };
        }
        return { ok: false, error: "未知消息类型" };
      })
      .then(sendResponse)
      .catch((error) => {
        sendResponse({
          ok: false,
          error: error instanceof Error ? error.message : "页面操作失败"
        });
      });

    return true;
  });
})();
