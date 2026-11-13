from __future__ import annotations

import json
from typing import Any

from browser_use.llm.messages import SystemMessage, UserMessage
from pydantic import BaseModel, Field


EXTRACT_QUESTIONS_SCRIPT = r"""
() => {
  const clean = (value) => String(value || "").replace(/\s+/g, " ").trim();
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
  const spanOptions = (root) => {
    const seen = new Set();
    const result = [];
    root.querySelectorAll(".stem_answer span[data]").forEach((element) => {
      const key = clean(element.getAttribute("data"));
      if (!key || seen.has(key)) return;
      seen.add(key);
      const parentText = clean(element.parentElement?.innerText || element.innerText);
      const option = annotateOption(element, key, parentText);
      if (option) result.push(option);
    });
    return result;
  };
  const textFields = (root) => {
    const iframes = Array.from(root.querySelectorAll("iframe"));
    return Array.from(root.querySelectorAll("textarea")).map((textarea, index) => {
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
  const questionNodes = Array.from(
    document.querySelectorAll(".questionLi[id^='question']")
  );

  questionNodes.forEach((root, questionIndex) => {
    const qid = root.id.replace("question", "");
    const heading = root.querySelector("h3");
    const type = clean(heading?.querySelector(".colorShallow")?.innerText || "");
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
        annotateHiddenAnswer(root, `answer${qid}`);
        const groups = sharedGroups.map((group, groupIndex) => {
          group.setAttribute("data-mumu-shared-index", String(groupIndex));
          const options = spanOptions(group);
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
          expected_values: groups.length,
        };
        question.unit_ids.push(unitId);
        units.push(unit);
      } else {
        const unitId = `q${qid}`;
        const options = roleOptions(root);
        const fields = textFields(root);
        const kind = questionKind(type, options.length > 0, fields.length);
        root.setAttribute("data-mumu-unit", unitId);
        root.setAttribute("data-mumu-kind", kind);
        annotateHiddenAnswer(root, `answer${qid}`);
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
    unit.file_inputs = root.querySelectorAll('input[type="file"]').length;
    const hiddenId = root.getAttribute('data-mumu-hidden-id');
    const hidden = hiddenId ? document.getElementById(hiddenId) : null;
    const selected = element => element.getAttribute('aria-checked') === 'true' || element.classList.contains('check_answer') || element.classList.contains('check_answer_dx');
    if (unit.kind === 'single' || unit.kind === 'multiple') {
      unit.filled = Boolean(hidden?.value?.trim()) || [...root.querySelectorAll('[data-mumu-option]')].some(selected);
    } else if (unit.kind === 'shared') {
      unit.filled = unit.groups.length > 0 && [...root.querySelectorAll('.B-answerCon')].every(group => [...group.querySelectorAll('[data-mumu-option]')].some(selected));
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
  return JSON.stringify({
    title: document.title,
    url: location.href,
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
  const isSelected = option => option.getAttribute('aria-checked') === 'true'
    || option.classList.contains('check_answer') || option.classList.contains('check_answer_dx');
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
    const questionRoot = root.closest(".questionLi[id^='question']");
    const questionId = questionRoot?.id?.replace("question", "") || "";
    if (values.some((value) => clean(value))) {
      if (questionId) answeredQuestionIds.add(questionId);
    }
    if (kind === "shared") {
      values.forEach((value, index) => {
        const key = clean(value);
        const group = root.querySelectorAll(".B-answerCon")[index];
        if (!group) return;
        const option = Array.from(group.querySelectorAll("span[data]")).find(
          (element) => element.getAttribute("data") === key
        );
        if (option) option.click();
      });
      root.querySelectorAll(".B-answerCon").forEach((group, index) => {
        const key = clean(values[index]);
        group.querySelectorAll("span[data]").forEach((element) => {
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
        hidden_id: hiddenId,
        hidden_value: hidden?.value || "",
      });
      return;
    }

    const fields = Array.from(
      root.querySelectorAll("textarea[data-mumu-field]")
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
      const complete = result.kind === 'shared'
        ? result.values.length === unit.querySelectorAll('.B-answerCon').length && result.values.every(value => clean(value))
        : ['single','multiple'].includes(result.kind) ? Boolean(result.hidden_value) && result.checked.length > 0
        : result.field_values.length > 0 && result.field_values.every(value => clean(value));
      unit.setAttribute('data-mumu-filled', String(complete));
    });
    const complete = unitRoots.length > 0 && unitRoots.every(unit => unit.getAttribute('data-mumu-filled') === 'true');
    document.getElementById(`answerSheet${questionId}`)?.classList.toggle('active', complete);
  });
  return JSON.stringify(results);
})
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
    result = await llm.ainvoke(
        [
            SystemMessage(content=system),
            UserMessage(content=user),
        ],
        output_format=QuizAnswerSheet,
    )
    return result.completion
