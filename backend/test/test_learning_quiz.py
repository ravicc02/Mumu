from __future__ import annotations

import asyncio
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from playwright.async_api import async_playwright

from app.config import Settings
from app.db import Database
from app.events import EventHub
from app.preferences import load_preferences
from app.services.assignments.quiz_adapter import (
    QuizAnswer,
    QuizAnswerSheet,
    extract_questions,
    fill_answers,
    generate_answers,
    submit_answers,
)
from app.services.learning.service import LearningService
from app.services.runtime.browser import chrome_executable


QUIZ_EXTRACTED = {
    "title": "章节测验",
    "url": "https://example.test/chapter-quiz",
    "question_count": 1,
    "unit_count": 1,
    "questions": [
        {
            "question_id": "1",
            "question_no": 1,
            "type": "单选题",
            "text": "1. 测试题目",
            "unit_ids": ["q1"],
        }
    ],
    "units": [
        {
            "unit_id": "q1",
            "question_id": "1",
            "question_no": 1,
            "type": "单选题",
            "kind": "single",
            "text": "1. 测试题目 A 正确 B 错误",
            "options": [{"key": "A", "label": "正确"}, {"key": "B", "label": "错误"}],
            "groups": [],
            "fields": [],
            "verification": "selection",
            "expected_values": 1,
        }
    ],
}


class FakeQuizLLM:
    async def ainvoke(self, messages, output_format):
        content = "\n".join(str(getattr(message, "content", "")) for message in messages)
        unit_ids = re.findall(r'"unit_id"\s*:\s*"([^"]+)"', content)
        return SimpleNamespace(
            completion=QuizAnswerSheet(
                answers=[QuizAnswer(unit_id=identifier, values=["A"]) for identifier in dict.fromkeys(unit_ids)]
            )
        )


class QuizLearningBrowser:
    instances = []

    def __init__(self, account, **kwargs):
        self.account = account
        self.submitted = False
        self.closed = False
        self.play_calls = 0
        self.__class__.instances.append(self)

    async def open(self):
        pass

    async def close(self):
        self.closed = True

    async def navigate(self, url):
        pass

    async def open_chapter(self, identifier):
        pass

    async def verify_catalog(self):
        return [{
            "chapterId": "quiz-chapter",
            "title": "章节测验",
            "remaining": 0 if self.submitted else 1,
            "completed": self.submitted,
        }]

    async def inspect(self):
        return (
            {"quiz": {"id": "quiz", "title": "章节测验", "mandatory": True, "completed": self.submitted, "kind": "quiz"}},
            [{"job_id": "quiz", "frame": self, "data": {"challenge": False, "quiz": True, "error": "", "media": None}}],
        )

    async def extract_quiz_questions(self, frame):
        return QUIZ_EXTRACTED

    async def fill_quiz_answers(self, frame, answer_sheet):
        return [{
            "unit_id": answer_sheet.answers[0].unit_id,
            "kind": "single",
            "values": answer_sheet.answers[0].values,
            "checked": answer_sheet.answers[0].values,
            "has_hidden": False,
            "hidden_value": "",
        }]

    async def submit_quiz_answers(self, frame):
        self.submitted = True
        return {"clicked": True, "confirmed": True, "reason": "提交成功"}

    async def quiz_is_open(self, frame):
        return not self.submitted

    async def pause(self):
        pass

    async def focus(self):
        pass

    async def snapshot(self):
        return {"image": "data:image/png;base64,cG5n", "url": "https://example.test/chapter-quiz"}

    async def evidence(self):
        yield {"index": 1, "count": 1, "image": b"png", "url": "https://example.test/chapter-quiz"}


class LearningQuizServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(data_root=Path(self.temp.name))
        self.settings.ensure_directories()
        self.db = Database(self.settings)
        self.db.initialize()
        account = self.db.create_account("fixture", "chaoxing", self.settings)
        self.db.update_account(account["id"], status="ready")
        self.service = LearningService(
            self.db,
            self.settings,
            EventHub(),
            QuizLearningBrowser,
            llm_factory=lambda settings: FakeQuizLLM(),
        )
        self.service.initialize()
        self.course = self.service.store.upsert_courses(
            account["id"],
            [{"courseId": "course", "classId": "class", "courseName": "章节测验课程", "url": "https://example.test/course"}],
            2026,
        )[0]
        self.notification = patch(
            "app.services.learning.service.send_completion_notification",
            AsyncMock(return_value={}),
        )
        self.notification.start()

    async def asyncTearDown(self):
        await self.service.shutdown()
        self.notification.stop()
        self.temp.cleanup()

    async def wait_until(self, predicate, timeout=10):
        async with asyncio.timeout(timeout):
            while not predicate():
                await asyncio.sleep(.02)

    async def test_chapter_quiz_uses_structured_answers_and_continues_after_platform_confirmation(self):
        self.assertTrue(load_preferences(self.db).learning.auto_submit_quiz)
        run = self.service.start(self.course["id"])
        await self.wait_until(lambda: self.service.store.run(run["id"])["status"] == "completed")
        browser = QuizLearningBrowser.instances[-1]
        events = self.service.store.events(run["id"])
        self.assertTrue(browser.submitted)
        self.assertTrue(any(event["kind"] == "quiz_extracted" for event in events))
        self.assertTrue(any(event["kind"] == "quiz_filled" for event in events))
        self.assertTrue(any(event["kind"] == "quiz_submitted" for event in events))
        self.assertTrue(any(event["kind"] == "completed" for event in events))
        self.assertEqual(self.service.store.run(run["id"])["completed"], ["quiz-chapter"])

    async def test_new_video_quiz_question_is_answered_before_the_chapter_moves_on(self):
        class SequentialQuizBrowser(QuizLearningBrowser):
            def __init__(self, account, **kwargs):
                super().__init__(account, **kwargs)
                self.submissions = 0

            async def verify_catalog(self):
                return [{
                    "chapterId": "quiz-chapter",
                    "title": "章节测验",
                    "remaining": 0 if self.submissions >= 2 else 1,
                    "completed": self.submissions >= 2,
                }]

            async def extract_quiz_questions(self, frame):
                question = "q1" if self.submissions == 0 else "q2"
                return {
                    **QUIZ_EXTRACTED,
                    "questions": [{**QUIZ_EXTRACTED["questions"][0], "question_id": question, "text": question}],
                    "units": [{**QUIZ_EXTRACTED["units"][0], "unit_id": question, "question_id": question, "text": "题目 " + question}],
                }

            async def submit_quiz_answers(self, frame):
                self.submissions += 1
                return {"clicked": True, "confirmed": False, "pending": True}

            async def quiz_is_open(self, frame):
                return self.submissions < 2

        self.service.browser_factory = SequentialQuizBrowser
        run = self.service.start(self.course["id"])
        await self.wait_until(lambda: self.service.store.run(run["id"])["status"] == "completed")
        browser = SequentialQuizBrowser.instances[-1]
        events = self.service.store.events(run["id"])
        self.assertEqual(browser.submissions, 2)
        self.assertEqual(sum(event["kind"] == "quiz_submitted" for event in events), 2)

    async def test_unverifiable_quiz_page_is_left_for_human_review(self):
        from app.services.learning.quiz import solve_learning_quiz

        browser = QuizLearningBrowser({})
        browser.submit_quiz_answers = AsyncMock(
            return_value={"clicked": False, "confirmed": False, "reason": "页面状态无法读取"}
        )
        browser.quiz_is_open = AsyncMock(side_effect=RuntimeError("page changed"))

        class Control:
            async def run(self, operation):
                result = operation()
                return await result if hasattr(result, "__await__") else result

        result = await solve_learning_quiz(
            browser=browser,
            frame=object(),
            unit={**QUIZ_EXTRACTED["units"][0], "title": "章节测验"},
            llm=FakeQuizLLM(),
            control=Control(),
            emit=AsyncMock(),
            auto_submit=True,
        )
        self.assertEqual(result["status"], "filled_pending_review")
        self.assertIn("避免重复提交", result["reason"])


class LearningQuizAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_plain_text_model_response_uses_strict_json_fallback(self):
        class PlainTextLLM:
            def __init__(self):
                self.calls = []

            async def ainvoke(self, messages, output_format=None):
                self.calls.append(output_format)
                if output_format is not None:
                    raise RuntimeError("Expected tool_calls in response but got none")
                return SimpleNamespace(
                    completion='```json\n{"units":[{"unit_id":"q1","answer":"A"}]}\n```'
                )

        llm = PlainTextLLM()
        result = await generate_answers(llm, QUIZ_EXTRACTED)
        self.assertEqual(result.answers, [QuizAnswer(unit_id="q1", values=["A"])])
        self.assertEqual(len(llm.calls), 2)
        self.assertIsNone(llm.calls[1])

    async def test_quiz_confirmation_dialog_accepts_non_native_button(self):
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, executable_path=str(chrome_executable()))
            try:
                page = await browser.new_page()
                await page.set_content("""
                    <div class="questionLi" id="question1">
                      <label><input type="radio" name="answer" value="A" checked>A 正确选项</label>
                    </div>
                    <button id="submit">提交</button>
                    <div id="confirm" class="dialog" style="display:none">
                      <button class="dialog-button" id="confirm-submit">提交</button>
                      <button class="dialog-button" id="cancel">取消</button>
                    </div>
                    <div id="feedback" role="status"></div>
                    <script>
                      document.querySelector('#submit').onclick = () => { document.querySelector('#confirm').style.display = 'block'; };
                      document.querySelector('#confirm-submit').onclick = () => {
                        document.querySelector('#confirm').style.display = 'none';
                        document.querySelector('#feedback').textContent = '提交成功';
                      };
                      document.querySelector('#cancel').onclick = () => { document.querySelector('#confirm').style.display = 'none'; };
                    </script>
                """)
                submitted = await submit_answers(page)
                self.assertTrue(submitted["clicked"])
                self.assertTrue(submitted["confirmed"])
            finally:
                await browser.close()

    async def test_quiz_confirmation_dialog_is_clicked_in_the_parent_frame(self):
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, executable_path=str(chrome_executable()))
            try:
                page = await browser.new_page()
                await page.set_content("""
                    <iframe id="quiz" style="width:800px;height:500px"></iframe>
                    <div id="workpop" class="maskDiv" style="display:none">
                      <a id="popok">提交</a><a id="popno">取消</a>
                    </div>
                """)
                frame = page.frames[1]
                await frame.set_content("""
                    <div class="questionLi" id="question1">
                      <label><input type="radio" name="answer" value="A" checked>A 正确选项</label>
                    </div>
                    <button id="submit">提交</button>
                    <div id="feedback" role="status"></div>
                """)
                await page.evaluate("""() => {
                  const frame = document.querySelector('#quiz');
                  frame.contentDocument.querySelector('#submit').onclick = () => {
                    document.querySelector('#workpop').style.display = 'block';
                  };
                  document.querySelector('#popok').onclick = () => {
                    document.querySelector('#workpop').style.display = 'none';
                    frame.contentDocument.querySelector('#feedback').textContent = '提交成功';
                  };
                }""")
                submitted = await submit_answers(frame)
                self.assertTrue(submitted["clicked"])
                self.assertTrue(submitted["confirmed"])
                self.assertEqual(submitted["dialog"]["text"], "提交")
            finally:
                await browser.close()

    async def test_repeated_platform_question_ids_receive_unique_answer_units(self):
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, executable_path=str(chrome_executable()))
            try:
                page = await browser.new_page()
                await page.set_content("""
                    <div class="questionLi" id="question0"><h3><span class="colorShallow">单选题</span>第一题</h3>
                      <label><input type="radio" name="q0" value="A">A 第一项</label>
                      <label><input type="radio" name="q0" value="B">B 第二项</label>
                    </div>
                    <div class="questionLi" id="question0"><h3><span class="colorShallow">单选题</span>第二题</h3>
                      <label><input type="radio" name="q1" value="A">A 第一项</label>
                      <label><input type="radio" name="q1" value="B">B 第二项</label>
                    </div>
                """)
                extracted = await extract_questions(page)
                self.assertEqual([unit["unit_id"] for unit in extracted["units"]], ["q1", "q2"])
                self.assertEqual([unit["question_id"] for unit in extracted["units"]], ["1", "2"])
            finally:
                await browser.close()

    async def test_video_quiz_controls_are_extracted_filled_and_submitted(self):
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, executable_path=str(chrome_executable()))
            try:
                page = await browser.new_page()
                await page.set_content("""
                    <div class="ans-videoquiz">
                      <div class="ans-videoquiz-title">视频中的测试题</div>
                      <label><input type="radio" name="answer" value="A">A 正确选项</label>
                      <label><input type="radio" name="answer" value="B">B 错误选项</label>
                      <button id="submit" type="button">提交</button>
                      <div id="feedback" role="status"></div>
                    </div>
                    <script>
                      document.querySelector('#submit').addEventListener('click', () => {
                        if (document.querySelector('input[value=A]').checked) {
                          document.querySelector('#feedback').textContent = '提交成功';
                        }
                      });
                    </script>
                """)
                extracted = await extract_questions(page)
                self.assertEqual(extracted["question_count"], 1)
                self.assertEqual([item["key"] for item in extracted["units"][0]["options"]], ["A", "B"])
                self.assertEqual(extracted["units"][0]["verification"], "selection")
                filled = await fill_answers(page, QuizAnswerSheet(answers=[QuizAnswer(unit_id=extracted["units"][0]["unit_id"], values=["A"])]))
                self.assertEqual(filled[0]["checked"], ["A"])
                submitted = await submit_answers(page)
                self.assertTrue(submitted["clicked"])
                self.assertTrue(submitted["confirmed"])
            finally:
                await browser.close()
