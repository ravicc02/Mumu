from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from browser_use.llm.messages import SystemMessage, UserMessage
from pydantic import BaseModel, Field


EXTRACT_QUESTIONS_SCRIPT = r"""
() => {
  const clean = (value) => String(value || "").replace(/\s+/g, " ").trim();
  const promptText = (root) => {
    const copy = root.cloneNode(true);
    copy.querySelectorAll("script,style,textarea,input,button,select,iframe,.colorShallow,.read_type,.question-type,[data-mumu-option],.edui-editor,.edui-toolbar,[contenteditable=true]").forEach(node => node.remove());
    return clean(copy.textContent);
  };
  const fileControls = (root) => {
    const files = [...root.querySelectorAll('input[type="file"]')].filter(node => !node.disabled);
    const optional = files.filter(node => !node.required && node.getAttribute('aria-required') !== 'true'
      && (node.closest('.edui-editor,.edui-toolbar,.tox,.cke,.editor-toolbar')
        || /可选|选传|非必填/.test(node.getAttribute('aria-label') || '')));
    return { file_inputs: files.length, required_file_inputs: files.length - optional.length, optional_file_inputs: optional.length };
  };
  const questionKind = (typeText, hasOptions, fieldCount) => {
    const text = String(typeText || "");
    if (text.includes("判断题")) return "single";
    if (text.includes("单选题")) return "single";
    if (text.includes("多选题")) return "multiple";
    if (text.includes("填空题")) return "fill";
    if (text.includes("简答题")) return "long";
    if (hasOptions) return "single";
    if (fieldCount > 0) return "long";
    return "unknown";
  };
  const isSelected = (element) => {
    const input = element.matches?.("input") ? element : element.querySelector?.("input");
    const className = String(element.className || "");
    return element.getAttribute?.("aria-checked") === "true"
      || Boolean(input?.checked)
      || /(?:^|\s)(?:check_answer|check_answer_dx|selected|active|checked)(?:\s|$)/.test(className);
  };
  const annotateOption = (element, key, label) => {
    const normalizedKey = clean(key);
    if (!normalizedKey) return null;
    element.setAttribute("data-mumu-option", normalizedKey);
    return {
      key: normalizedKey,
      label: clean(label),
    };
  };
  const roleOptions = (root) => {
    const seen = new Set();
    const result = [];
    root.querySelectorAll("div[role=radio], div[role=checkbox]").forEach((element) => {
      if (!element.getClientRects().length || element.getAttribute('aria-disabled') === 'true' || element.querySelector('input:disabled')) return;
      const aria = clean(element.getAttribute("aria-label"));
      const match = aria.match(/^([A-F])\b/i);
      const key = match ? match[1].toUpperCase() : "";
      if (!key || seen.has(key)) return;
      seen.add(key);
      const option = annotateOption(element, key, aria.replace(/选择$/, ""));
      if (option) result.push(option);
    });
    return result;
  };
  const spanOptions = (root, selector = ".stem_answer span[data]") => {
    const seen = new Set();
    const result = [];
    root.querySelectorAll(selector).forEach((element) => {
      if (!element.getClientRects().length || getComputedStyle(element).visibility === 'hidden'
        || element.closest('[aria-disabled="true"],[inert]')) return;
      const key = clean(element.getAttribute("data"));
      if (!key || seen.has(key)) return;
      seen.add(key);
      const parentText = clean(element.parentElement?.innerText || element.innerText);
      const option = annotateOption(element, key, parentText);
      if (option) result.push(option);
    });
    return result;
  };
  const genericOptions = (root) => {
    const seen = new Set();
    const result = [];
    const candidates = [
      ...root.querySelectorAll("input[type=radio],input[type=checkbox]"),
      ...root.querySelectorAll("[role=radio],[role=checkbox],.ans-videoquiz-opt,[class*='option']"),
    ];
    candidates.forEach((input, index) => {
      if (input.disabled || input.getAttribute('aria-disabled') === 'true') return;
      const element = input.matches("input")
        ? input.closest("label,li,.ans-videoquiz-opt,[class*='option']") || input
        : input;
      if (element.getAttribute('aria-disabled') === 'true' || element.querySelector('input:disabled')) return;
      if (element.closest("[data-mumu-option]") || !element.getClientRects().length) return;
      const text = clean(element.innerText || element.textContent || input.value || "");
      if (!text || /^(提交|交卷|继续|确定|确认|下一题)$/.test(text)) return;
      const labeled = text.slice(0, 80).match(/(?:^|\s)([A-F])(?:[.、:：)\s]|$)/i);
      const rawValue = clean(input.value);
      const key = labeled?.[1]?.toUpperCase()
        || (/^[A-F]$/i.test(rawValue) ? rawValue.toUpperCase() : "")
        || String.fromCharCode(65 + index);
      if (!key || seen.has(key)) return;
      seen.add(key);
      const option = annotateOption(element, key, text);
      if (option) result.push(option);
    });
    return result;
  };
  const textFields = (root) => {
    const iframes = Array.from(root.querySelectorAll("iframe"));
    return Array.from(root.querySelectorAll('textarea,input[type=text],input[type=number],input:not([type])'))
      .filter(node => !node.disabled && !node.readOnly).map((textarea, index) => {
      const iframe = iframes[index] || null;
      textarea.setAttribute("data-mumu-field", String(index));
      if (iframe?.id) {
        textarea.setAttribute("data-mumu-iframe", iframe.id);
      }
      return {
        index,
        textarea_id: textarea.id || "",
        textarea_name: textarea.name || "",
        iframe_id: iframe?.id || "",
      };
    });
  };
  const annotateHiddenAnswer = (root, hiddenId) => {
    if (!hiddenId) return;
    root.setAttribute("data-mumu-hidden-id", hiddenId);
  };

  const questions = [];
  const units = [];
  const questionNodes = Array.from(document.querySelectorAll(".questionLi[id^='question']"));
  if (!questionNodes.length) {
    document.querySelectorAll(".TiMu").forEach((element) => {
      if (element.getClientRects().length) questionNodes.push(element);
    });
  }
  document.querySelectorAll(".ans-videoquiz").forEach((element) => {
    if (element.getClientRects().length && !element.closest(".questionLi[id^='question'],.TiMu")) {
      questionNodes.push(element);
    }
  });

  questionNodes.forEach((root, questionIndex) => {
    const rawQid = String(root.id || "").replace("question", "")
      || clean(root.getAttribute("data-qid") || root.getAttribute("data"))
      || `video${questionIndex + 1}`;
    const qid = String(questionIndex + 1);
    const heading = root.querySelector("h3,.ans-videoquiz-title,.Zy_TItle");
    const type = clean(heading?.querySelector(".colorShallow")?.innerText
      || root.getAttribute("data-type")
      || root.querySelector(".read_type,.question-type")?.innerText
      || "");
    const questionText = clean(
      heading?.innerText || root.innerText.slice(0, 1200)
    );
    const question = {
      question_id: qid,
      question_no: questionIndex + 1,
      type,
      text: questionText,
      unit_ids: [],
    };

    const readingBlocks = Array.from(root.querySelectorAll(".reading_answer"));
    if (readingBlocks.length > 0) {
      readingBlocks.forEach((block, blockIndex) => {
        const blockType = clean(
          block.querySelector(".read_type")?.innerText || type
        );
        const options = spanOptions(block);
        const fields = textFields(block);
        const kind = questionKind(blockType, options.length > 0, fields.length);
        const unitId = `q${qid}:r${blockIndex}`;
        block.setAttribute("data-mumu-unit", unitId);
        block.setAttribute("data-mumu-kind", kind);
        const blockData = clean(block.getAttribute("data"));
        const hiddenId = blockData
          ? `answer${blockData}`
          : clean(block.querySelector("input[id^='answer']")?.id || "");
        annotateHiddenAnswer(block, hiddenId);
        const unit = {
          unit_id: unitId,
          question_id: qid,
          question_no: questionIndex + 1,
          type: blockType,
          kind,
          text: clean(block.innerText).slice(0, 1400),
          options,
          groups: [],
          fields,
          verification: hiddenId ? "hidden" : "selection",
          expected_values: kind === "single" ? 1
            : kind === "multiple" ? options.length
            : fields.length,
        };
        question.unit_ids.push(unitId);
        units.push(unit);
      });
    } else {
      const sharedGroups = Array.from(root.querySelectorAll(".B-answerCon"));
      if (sharedGroups.length > 0) {
        const unitId = `q${qid}`;
        root.setAttribute("data-mumu-unit", unitId);
        root.setAttribute("data-mumu-kind", "shared");
        annotateHiddenAnswer(root, `answer${rawQid}`);
        const groups = sharedGroups.map((group, groupIndex) => {
          group.setAttribute("data-mumu-shared-index", String(groupIndex));
          const options = spanOptions(group, ":scope > span[data], .stem_answer span[data]");
          return {
            index: groupIndex,
            text: clean(group.parentElement?.innerText || group.innerText).slice(0, 500),
            options: options.map((option) => option.key),
          };
        });
        const unit = {
          unit_id: unitId,
          question_id: qid,
          question_no: questionIndex + 1,
          type,
          kind: "shared",
          text: clean(root.innerText).slice(0, 2400),
          options: [],
          groups,
          fields: [],
          verification: "hidden",
          expected_values: groups.length,
        };
        question.unit_ids.push(unitId);
        units.push(unit);
      } else {
        const unitId = `q${qid}`;
        const allOptions = [...roleOptions(root), ...spanOptions(root), ...genericOptions(root)];
        const options = allOptions.filter((option, index) => allOptions.findIndex(item => item.key === option.key) === index);
        const fields = textFields(root);
        const choiceType = root.querySelector("input[type=checkbox],[role=checkbox]") ? `${type} 多选题` : type;
        const kind = questionKind(choiceType, options.length > 0, fields.length);
        root.setAttribute("data-mumu-unit", unitId);
        root.setAttribute("data-mumu-kind", kind);
        const hiddenId = clean(root.querySelector("input[id^='answer']")?.id || "");
        annotateHiddenAnswer(root, hiddenId);
        const unit = {
          unit_id: unitId,
          question_id: qid,
          question_no: questionIndex + 1,
          type,
          kind,
          text: clean(root.innerText).slice(0, 2400),
          options,
          groups: [],
          fields,
          verification: hiddenId ? "hidden" : "selection",
          expected_values: kind === "single" ? 1
            : kind === "multiple" ? options.length
            : fields.length,
        };
        question.unit_ids.push(unitId);
        units.push(unit);
      }
    }
    questions.push(question);
  });

  units.forEach(unit => {
    const root = [...document.querySelectorAll('[data-mumu-unit]')].find(node => node.getAttribute('data-mumu-unit') === unit.unit_id);
    if (!root) return;
    const question = questions.find(item => item.question_id === unit.question_id);
    unit.prompt_text = clean((unit.unit_id.includes(':r') ? (question?.text || '') + ' ' : '') + promptText(root));
    Object.assign(unit, fileControls(root));
    const hiddenId = root.getAttribute('data-mumu-hidden-id');
    const hidden = hiddenId ? document.getElementById(hiddenId) : null;
    if (unit.kind === 'single' || unit.kind === 'multiple') {
      unit.filled = Boolean(hidden?.value?.trim()) || [...root.querySelectorAll('[data-mumu-option]')].some(isSelected);
    } else if (unit.kind === 'shared') {
      unit.filled = unit.groups.length > 0 && [...root.querySelectorAll('.B-answerCon')].every(group => [...group.querySelectorAll('[data-mumu-option]')].some(isSelected));
    } else {
      unit.filled = unit.fields.length > 0 && unit.fields.every(field => {
        const textarea = [...root.querySelectorAll('[data-mumu-field]')].find(node => Number(node.getAttribute('data-mumu-field')) === field.index);
        let content = textarea?.value || '';
        try {
          const frame = field.iframe_id ? document.getElementById(field.iframe_id) : null;
          const body = frame?.contentDocument?.body;
          if (body) content = body.innerText || (body.querySelector('img,video,audio,a[href]') ? 'attachment' : '');
        } catch { /* Keep the native textarea when a frame is cross-origin. */ }
        return content.replace(/<[^>]*>/g, '').replace(/&nbsp;|\s|\u200b/g, '').length > 0;
      });
    }
  });
  const instructions = document.body.cloneNode(true);
  instructions.querySelectorAll(".questionLi,.TiMu,.ans-videoquiz,[data-mumu-unit],script,style,textarea,input,button,select,iframe,nav,header,footer,.edui-editor,.edui-toolbar,a,[role=button]").forEach(node => node.remove());
  return JSON.stringify({
    title: document.title,
    url: location.href,
    page_text: clean(document.body?.innerText || "").slice(0, 6000),
    page_instructions: clean(instructions.textContent).slice(0, 6000),
    ...fileControls(document),
    question_count: questions.length,
    unit_count: units.length,
    questions,
    units,
  });
}
"""


FILL_ANSWERS_SCRIPT = r"""
(async (answersJson) => {
  const clean = (value) => String(value || "").trim();
  const answers = JSON.parse(answersJson);
  const byId = new Map(
    (answers.answers || []).map((item) => [item.unit_id, item.values || []])
  );
  const results = [];
  const answeredQuestionIds = new Set();
  const isSelected = (option) => {
    const input = option.matches?.('input') ? option : option.querySelector?.('input');
    const className = String(option.className || '');
    return option.getAttribute?.('aria-checked') === 'true'
      || Boolean(input?.checked)
      || /(?:^|\s)(?:check_answer|check_answer_dx|selected|active|checked)(?:\s|$)/.test(className);
  };
  const setField = (textarea, iframeId, value) => {
    const text = clean(value);
    const html = `<p>${text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\n/g, '<br>')}</p>`;
    textarea.value = text;
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
    textarea.dispatchEvent(new Event("change", { bubbles: true }));
    const iframe = iframeId ? document.getElementById(iframeId) : null;
    if (iframe) {
      try {
        const doc = iframe.contentDocument || iframe.contentWindow.document;
        if (doc?.body) {
          doc.body.innerHTML = html;
          doc.body.dispatchEvent(new Event("input", { bubbles: true }));
          doc.body.dispatchEvent(new Event("change", { bubbles: true }));
        }
      } catch (_) {}
      try {
        const editor = window.UE?.getEditor?.(iframeId);
        if (editor) {
          editor.setContent(html);
          editor.sync();
        }
      } catch (_) {}
    }
    return textarea.value;
  };

  document.querySelectorAll("[data-mumu-unit]").forEach((root) => {
    const unitId = root.getAttribute("data-mumu-unit");
    if (!byId.has(unitId)) return;
    const kind = root.getAttribute("data-mumu-kind");
    const values = byId.get(unitId) || [];
    const questionRoot = root.closest(".questionLi[id^='question'],.TiMu,.ans-videoquiz");
    const questionId = questionRoot?.id?.replace("question", "") || "";
    if (values.some((value) => clean(value))) {
      if (questionId) answeredQuestionIds.add(questionId);
    }
    if (kind === "shared") {
      values.forEach((value, index) => {
        const key = clean(value);
        const group = root.querySelectorAll(".B-answerCon")[index];
        if (!group) return;
        const option = Array.from(group.querySelectorAll("[data-mumu-option]")).find(
          (element) => element.getAttribute("data-mumu-option") === key
        );
        if (option && !isSelected(option)) option.click();
      });
      root.querySelectorAll(".B-answerCon").forEach((group, index) => {
        const key = clean(values[index]);
        group.querySelectorAll("[data-mumu-option]").forEach((element) => {
          element.classList.toggle(
            "check_answer",
            element.getAttribute("data") === key
          );
        });
      });
      const hiddenId = root.getAttribute("data-mumu-hidden-id") || "";
      const hidden = hiddenId ? document.getElementById(hiddenId) : null;
      if (hidden) {
        hidden.value = JSON.stringify(
          values.map((content, index) => ({
            name: index + 1,
            content: clean(content),
          }))
        );
        hidden.dispatchEvent(new Event("input", { bubbles: true }));
        hidden.dispatchEvent(new Event("change", { bubbles: true }));
      }
      results.push({
        unit_id: unitId,
        kind,
        values,
        hidden_id: hiddenId,
        hidden_value: hidden?.value || "",
      });
      return;
    }

    if (kind === "single" || kind === "multiple") {
      const choices = new Set(values.map((value) => clean(value)));
      root.querySelectorAll("[data-mumu-option]").forEach((option) => {
        const key = option.getAttribute("data-mumu-option");
        const selected = isSelected(option);
        if ((kind === 'multiple' && choices.has(key) !== selected) || (kind === 'single' && choices.has(key) && !selected)) option.click();
      });
      const checked = Array.from(
        root.querySelectorAll("[data-mumu-option]")
      )
        .filter(isSelected)
        .map((option) => option.getAttribute("data-mumu-option"));
      const hiddenId = root.getAttribute("data-mumu-hidden-id") || "";
      const hidden = hiddenId ? document.getElementById(hiddenId) : null;
      results.push({
        unit_id: unitId,
        kind,
        values,
        checked,
        has_hidden: Boolean(hidden),
        hidden_id: hiddenId,
        hidden_value: hidden?.value || "",
      });
      return;
    }

    const fields = Array.from(
      root.querySelectorAll("[data-mumu-field]")
    );
    const fieldValues = fields.map((field, index) =>
      setField(
        field,
        field.getAttribute("data-mumu-iframe") || "",
        values[index] || ""
      )
    );
    results.push({
      unit_id: unitId,
      kind,
      values,
      field_values: fieldValues,
    });
  });
  await new Promise((resolve) => setTimeout(resolve, 400));
  results.forEach((result) => {
    const hiddenId = result.hidden_id || "";
    if (!hiddenId) return;
    const hidden = document.getElementById(hiddenId);
    if (!hidden) return;
    if (!hidden.value && result.kind !== "shared") {
      hidden.value = (result.values || []).map((value) => clean(value)).join("");
      hidden.dispatchEvent(new Event("input", { bubbles: true }));
      hidden.dispatchEvent(new Event("change", { bubbles: true }));
    }
    result.hidden_value = hidden.value || "";
    const root = [...document.querySelectorAll('[data-mumu-unit]')]
      .find(node => node.getAttribute('data-mumu-unit') === result.unit_id);
    if (root && ['single', 'multiple'].includes(result.kind)) {
      result.checked = [...root.querySelectorAll('[data-mumu-option]')]
        .filter(isSelected)
        .map(option => option.getAttribute('data-mumu-option'));
    }
  });
  answeredQuestionIds.forEach((questionId) => {
    const question = document.getElementById(`question${questionId}`);
    if (!question) return;
    const unitRoots = question.matches('[data-mumu-unit]') ? [question]
      : [...question.querySelectorAll('[data-mumu-unit]')];
    results.forEach(result => {
      const unit = unitRoots.find(node => node.getAttribute('data-mumu-unit') === result.unit_id);
      if (!unit) return;
      const requiresHidden = Boolean(unit.getAttribute('data-mumu-hidden-id'));
      const complete = result.kind === 'shared'
        ? result.values.length === unit.querySelectorAll('.B-answerCon').length && result.values.every(value => clean(value))
        : ['single','multiple'].includes(result.kind) ? (!requiresHidden || Boolean(result.hidden_value)) && result.checked.length > 0
        : result.field_values.length > 0 && result.field_values.every(value => clean(value));
      unit.setAttribute('data-mumu-filled', String(complete));
    });
    const complete = unitRoots.length > 0 && unitRoots.every(unit => unit.getAttribute('data-mumu-filled') === 'true');
    document.getElementById(`answerSheet${questionId}`)?.classList.toggle('active', complete);
  });
  return JSON.stringify(results);
})
"""


SUBMIT_ANSWERS_SCRIPT = r"""
(async () => {
  const clean = (value) => String(value || "").replace(/\s+/g, " ").trim();
  const visible = (node) => Boolean(node?.getClientRects().length)
    && getComputedStyle(node).visibility !== "hidden"
    && getComputedStyle(node).display !== "none";
  const questionRoots = [...document.querySelectorAll(".questionLi[id^='question'],.TiMu,.ans-videoquiz")]
    .filter(visible);
  if (!questionRoots.length) {
    return JSON.stringify({ clicked: false, confirmed: false, reason: "当前页面没有可提交的题目" });
  }
  const videoQuiz = document.querySelector(".ans-videoquiz");
  const scope = questionRoots[0].closest("form") || document;
  const controls = (root) => [...root.querySelectorAll(
    "button,a,input[type=button],input[type=submit],[role=button],[onclick],[class*='btn'],[class*='button'],.layui-layer-btn0,.layui-layer-btn1,.el-button,.ant-btn"
  )]
    .filter((node) => visible(node) && !node.disabled && node.getAttribute("aria-disabled") !== "true");
  const submitLabels = new Set(["提交", "交卷", "提交答案", "提交作业", "完成测验", "完成答题", "保存并提交"]);
  let submit = controls(scope).find((node) => submitLabels.has(clean(node.innerText || node.value || node.textContent)))
    || controls(document).find((node) => submitLabels.has(clean(node.innerText || node.value || node.textContent))
      && !node.closest(".questionLi,.TiMu"));
  if (!submit && videoQuiz) {
    const quizLabels = new Set(["确定", "确认", "继续", "继续学习", "下一题"]);
    submit = controls(videoQuiz).find((node) => quizLabels.has(clean(node.innerText || node.value || node.textContent)));
  }
  if (!submit) {
    return JSON.stringify({ clicked: false, confirmed: false, reason: "未找到可用的题目提交入口" });
  }
  if (submit.getAttribute("data-mumu-submit-clicked") === "true") {
    return JSON.stringify({ clicked: true, confirmed: false, pending: true, already_clicked: true, reason: "本次页面已点击过提交" });
  }
  submit.setAttribute("data-mumu-submit-clicked", "true");

  const feedbackSelector = "#dialogToast,.toast,[role=status],[role=alert],.layui-layer-content,.el-message__content,.ant-message-notice-content,.ans-videoquiz";
  const beforeFeedback = new Set([...document.querySelectorAll(feedbackSelector)].filter(visible)
    .map((node) => clean(node.innerText || node.textContent)));
  const beforeUrl = location.href;
  submit.click();
  await new Promise((resolve) => setTimeout(resolve, 700));

  const confirmLabels = new Set(["确定", "确认", "确认提交", "继续提交", "提交", "知道了"]);
  for (let attempt = 0; attempt < 10; attempt++) {
    const dialogRoots = [...document.querySelectorAll(".layui-layer,.modal,.dialog,[role=dialog],.el-dialog,.ant-modal,.ans-videoquiz")]
      .filter(visible);
    for (const root of dialogRoots) {
      const confirmation = controls(root).find((node) => node !== submit
        && confirmLabels.has(clean(node.innerText || node.value || node.textContent)));
      if (confirmation) {
        confirmation.click();
        attempt = 10;
        break;
      }
    }
    if (attempt >= 10) break;
    await new Promise((resolve) => setTimeout(resolve, 300));
  }

  const deadline = Date.now() + 900;
  while (Date.now() < deadline) {
    const messages = [...document.querySelectorAll(feedbackSelector)].filter(visible)
      .map((node) => clean(node.innerText || node.textContent))
      .filter(Boolean);
    const fresh = messages.filter((message) => !beforeFeedback.has(message));
    const error = fresh.find((message) => /提交失败|交卷失败|保存失败|答案错误|回答错误|未完成全部题目|请完成.*题/.test(message));
    if (error) {
      return JSON.stringify({ clicked: true, confirmed: false, failed: true, reason: error });
    }
    const success = fresh.find((message) => /提交成功|交卷成功|已提交|已完成测验|测验已完成|保存成功/.test(message));
    if (success) {
      return JSON.stringify({ clicked: true, confirmed: true, reason: success });
    }
    const questionsVisible = questionRoots.some((node) => node.isConnected && visible(node));
    if (location.href !== beforeUrl || !questionsVisible) {
      return JSON.stringify({ clicked: true, confirmed: false, pending: true, reason: "提交请求已发出，页面已离开题目状态" });
    }
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  return JSON.stringify({
    clicked: true,
    confirmed: false,
    pending: true,
    reason: "提交请求已发出，等待平台目录或回执确认",
  });
})()
"""


CONFIRM_SUBMIT_SCRIPT = r"""
() => {
  const clean = (value) => String(value || "").replace(/\s+/g, " ").trim();
  const visible = (node) => Boolean(node?.getClientRects().length)
    && getComputedStyle(node).visibility !== "hidden"
    && getComputedStyle(node).display !== "none";
  const roots = [...document.querySelectorAll(
    "#workpop,.popDiv,[role=dialog],.layui-layer,.modal,.dialog,.el-dialog,.ant-modal"
  )].filter(visible);
  const labels = new Set(["提交", "确定", "确认", "确认提交", "继续提交"]);
  for (const root of roots) {
    const controls = [...root.querySelectorAll(
      "button,a,input[type=button],input[type=submit],[role=button],[onclick]"
    )].filter((node) => visible(node) && !node.disabled && node.getAttribute("aria-disabled") !== "true");
    const confirm = controls.find((node) => labels.has(clean(node.innerText || node.value || node.textContent)));
    if (!confirm) continue;
    const text = clean(confirm.innerText || confirm.value || confirm.textContent);
    confirm.click();
    return JSON.stringify({
      clicked: true,
      text,
      root: clean(root.innerText || root.textContent).slice(0, 240),
    });
  }
  return JSON.stringify({ clicked: false });
}
"""


SUBMIT_FEEDBACK_SCRIPT = r"""
() => {
  const clean = (value) => String(value || "").replace(/\s+/g, " ").trim();
  const visible = (node) => Boolean(node?.getClientRects().length)
    && getComputedStyle(node).visibility !== "hidden"
    && getComputedStyle(node).display !== "none";
  const messages = [...document.querySelectorAll(
    "#dialogToast,.toast,[role=status],[role=alert],.layui-layer-content,"
    + ".el-message__content,.ant-message-notice-content,.ans-videoquiz,#worktoast,.mask-no-bg"
  )].filter(visible).map((node) => clean(node.innerText || node.textContent))
    .filter(Boolean);
  const questions = [...document.querySelectorAll(".questionLi[id^='question'],.TiMu,.ans-videoquiz")]
    .filter(visible).length;
  return JSON.stringify({ url: location.href, title: document.title, messages, questions });
}
"""


class QuizAnswer(BaseModel):
    unit_id: str
    values: list[str] = Field(default_factory=list)


class QuizAnswerSheet(BaseModel):
    answers: list[QuizAnswer]


async def extract_questions(page: Any) -> dict[str, Any]:
    raw = await page.evaluate(EXTRACT_QUESTIONS_SCRIPT)
    if isinstance(raw, str):
        return json.loads(raw)
    return raw


async def fill_answers(
    page: Any,
    answer_sheet: QuizAnswerSheet,
) -> list[dict[str, Any]]:
    raw = await page.evaluate(
        FILL_ANSWERS_SCRIPT,
        answer_sheet.model_dump_json(),
    )
    if isinstance(raw, str):
        return json.loads(raw)
    return raw


def _decode_script_result(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str):
        return json.loads(raw)
    return raw


async def _submission_snapshot(frame: Any) -> dict[str, Any]:
    try:
        return _decode_script_result(await frame.evaluate(SUBMIT_FEEDBACK_SCRIPT))
    except Exception:
        return {}


async def _submission_snapshots(frames: list[Any]) -> dict[int, dict[str, Any]]:
    result = {}
    for frame in frames:
        snapshot = await _submission_snapshot(frame)
        if snapshot:
            result[id(frame)] = snapshot
    return result


def _is_submission_response(response: Any) -> bool:
    try:
        method = str(response.request.method or "").upper()
        url = str(response.url or "").lower()
    except Exception:
        return False
    return method in {"POST", "PUT"} and "/work/" in url and "/validate" not in url


async def _submission_response_result(response: Any) -> dict[str, Any]:
    try:
        data = await response.json()
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    message = str(data.get("msg") or data.get("message") or "").strip()
    status = data.get("status")
    success = status in {True, 1, "1", "true", "success"} or data.get("result") in {True, 1, "1", "success"}
    failed = status in {False, 0, "0", "false", "failure"} and bool(message)
    if success:
        return {"confirmed": True, "reason": message or "平台已确认提交", "data": data}
    if failed or re.search(r"失败|错误|异常|未完成|不能提交", message):
        return {"clicked": True, "confirmed": False, "failed": True, "reason": message or "平台拒绝提交", "data": data}
    return {"data": data}


async def submit_answers(page: Any) -> dict[str, Any]:
    playwright_page = getattr(page, "page", page)
    frames = list(playwright_page.frames)
    before = await _submission_snapshots(frames)
    dialogs = []

    def handle_dialog(dialog):
        message = str(dialog.message or "")
        accepted = dialog.type == "confirm" and bool(re.search(r"确认.*提交|提交.*吗|交卷", message))
        dialogs.append({"type": dialog.type, "message": message, "accepted": accepted})
        asyncio.create_task((dialog.accept if accepted else dialog.dismiss)())

    playwright_page.on("dialog", handle_dialog)
    response_task = asyncio.create_task(playwright_page.wait_for_event(
        "response",
        predicate=_is_submission_response,
        timeout=15000,
    ))
    try:
        initial = _decode_script_result(await page.evaluate(SUBMIT_ANSWERS_SCRIPT))
        if initial.get("failed") or not initial.get("clicked") or initial.get("confirmed"):
            return initial

        confirmed_dialog = {}
        for _ in range(20):
            if any(item["accepted"] for item in dialogs):
                confirmed_dialog = {"clicked": True, "text": "浏览器确认框"}
                break
            for frame in list(playwright_page.frames):
                try:
                    result = _decode_script_result(await frame.evaluate(CONFIRM_SUBMIT_SCRIPT))
                except Exception:
                    continue
                if result.get("clicked"):
                    confirmed_dialog = result
                    break
            if confirmed_dialog:
                break
            await asyncio.sleep(0.25)

        deadline = asyncio.get_running_loop().time() + 12
        pending = False
        while asyncio.get_running_loop().time() < deadline:
            if response_task.done():
                try:
                    outcome = await _submission_response_result(response_task.result())
                except Exception:
                    outcome = {}
                if outcome.get("failed"):
                    return {**initial, **outcome}
                if outcome.get("confirmed"):
                    for frame in list(playwright_page.frames):
                        try:
                            follow_up = _decode_script_result(await frame.evaluate(CONFIRM_SUBMIT_SCRIPT))
                        except Exception:
                            continue
                        if follow_up.get("clicked"):
                            break
                    return {**initial, **outcome, "pending": False, "dialog": confirmed_dialog}

            current = await _submission_snapshots(list(playwright_page.frames))
            messages = [
                message
                for frame_id, snapshot in current.items()
                for message in snapshot.get("messages", [])
                if message not in before.get(frame_id, {}).get("messages", [])
            ]
            dialog_messages = [item["message"] for item in dialogs if item["message"]]
            error = next((message for message in [*messages, *dialog_messages]
                          if re.search(r"提交失败|交卷失败|保存失败|答案错误|回答错误|未完成全部题目|请完成.*题|账号发生异常", message)), "")
            if error:
                return {**initial, "clicked": True, "confirmed": False, "failed": True, "reason": error}
            success = next((message for message in messages
                            if re.search(r"提交成功|交卷成功|已提交|已完成测验|测验已完成|保存成功", message)), "")
            if success:
                return {
                    **initial,
                    "confirmed": True,
                    "pending": False,
                    "reason": success,
                    "dialog": confirmed_dialog,
                }

            page_snapshot = current.get(id(page), {})
            if before.get(id(page), {}).get("questions", 0) > 0 and page_snapshot.get("questions") == 0:
                pending = True
            if initial.get("before_url") and page_snapshot.get("url") not in {"", initial.get("before_url")}:
                pending = True
            if pending:
                reason = "已确认提交，题目页面已离开" if confirmed_dialog else "提交请求已发出，页面已离开题目状态"
                return {
                    **initial,
                    "confirmed": False,
                    "pending": True,
                    "reason": reason,
                    "dialog": confirmed_dialog,
                }
            await asyncio.sleep(0.3)

        reason = (
            "已确认平台提交弹窗，等待平台目录或回执确认"
            if confirmed_dialog else
            "提交请求已发出，等待平台目录或回执确认"
        )
        return {
            **initial,
            "confirmed": False,
            "pending": True,
            "reason": reason,
            "dialog": confirmed_dialog,
            "dialogs": dialogs,
        }
    finally:
        playwright_page.remove_listener("dialog", handle_dialog)
        if not response_task.done():
            response_task.cancel()
        try:
            await response_task
        except (asyncio.CancelledError, TimeoutError):
            pass
        except Exception:
            pass


async def generate_answers(
    llm: Any,
    extracted: dict[str, Any],
) -> QuizAnswerSheet:
    units = [
        {
            "unit_id": unit["unit_id"],
            "question_no": unit["question_no"],
            "type": unit["type"],
            "kind": unit["kind"],
            "text": unit["text"],
            "options": unit.get("options") or [],
            "groups": unit.get("groups") or [],
            "field_count": len(unit.get("fields") or []),
            "expected_values": unit.get("expected_values", 0),
        }
        for unit in extracted.get("units") or []
    ]
    system = """你是课程答题器。只返回结构化答案，不要解释。
规则：
1. 每个 unit_id 必须返回一次，不得新增或遗漏。
2. single 只返回一个选项 key。
3. multiple 返回所有正确选项 key。
4. fill 按 field_count 返回每个空格的答案。
5. long 返回一个完整、可直接提交的中文答案。
6. shared 按 groups 顺序返回每组一个选项 key。
7. 只使用题目给出的选项 key，不要自行创造 key。"""
    user = (
        "请完成下面的全部题目，并严格按给定结构返回：\n"
        + json.dumps({"units": units}, ensure_ascii=False)
    )
    messages = [
        SystemMessage(content=system),
        UserMessage(content=user),
    ]
    try:
        result = await llm.ainvoke(messages, output_format=QuizAnswerSheet)
    except Exception as error:
        if "tool_calls" not in str(error).lower():
            raise
        fallback = await llm.ainvoke([
            SystemMessage(content=system + "\n8. 直接返回 JSON 对象，不使用 Markdown 代码块或解释文字。"),
            UserMessage(content=user),
        ])
        return _answer_sheet_from_completion(fallback.completion)
    return result.completion


def _answer_sheet_from_completion(value: Any) -> QuizAnswerSheet:
    if isinstance(value, QuizAnswerSheet):
        return value
    if isinstance(value, dict):
        payload = value
    else:
        text = str(value or "").strip()
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            text = text[start:end + 1]
        payload = json.loads(text)
    answers = payload.get("answers") if isinstance(payload, dict) else None
    units = payload.get("units") if isinstance(payload, dict) else None
    if answers is None and isinstance(units, list):
        answers = units
    if answers is None and isinstance(payload, dict):
        answers = [{"unit_id": key, "values": answer} for key, answer in payload.items()]
    normalized = []
    for item in answers or []:
        if not isinstance(item, dict) or not item.get("unit_id"):
            continue
        values = item.get("values")
        if values is None:
            values = item.get("answer", item.get("answers", item.get("value")))
        if isinstance(values, (list, tuple)):
            values = [str(value).strip() for value in values if str(value).strip()]
        else:
            value = str(values or "").strip()
            if re.fullmatch(r"[A-F](?:\s*[,，]\s*[A-F])+", value, flags=re.I):
                values = [part.strip().upper() for part in re.split(r"\s*[,，]\s*", value)]
            elif re.fullmatch(r"[A-F]{2,}", value, flags=re.I):
                values = list(value.upper())
            else:
                values = [value] if value else []
        normalized.append({"unit_id": str(item["unit_id"]), "values": values})
    return QuizAnswerSheet.model_validate({"answers": normalized})
