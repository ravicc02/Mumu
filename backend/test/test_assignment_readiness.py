from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from playwright.async_api import async_playwright

from app.config import Settings
from app.db import Database
from app.services.assignments.readiness import assess_questions, execution_block, unit_requirements
from app.services.runtime.browser import chrome_executable
from app.services.assignments.quiz_adapter import extract_questions
from app.services.tasks import runner as runner

EXPERIMENT = "按附件中的要求完成实验。提交材料：（1）软件工程实验报告-第1组-张三.docx（2）经维护后的源程序代码-小米便签.rar（3）演示视频.mp4"
PRACTICE = "认真阅读附件文档，按照综合实践报告模板撰写报告，最终提交压缩包，内容包括：综合实践报告.docx、项目源码压缩包.rar、演示视频.mp4。"


class AssignmentReadinessTests(unittest.IsolatedAsyncioTestCase):
    def test_material_requirements_are_detected_even_with_a_normal_text_editor(self):
        for text in (EXPERIMENT, PRACTICE):
            assessment = assess_questions({"question_count": 1, "units": [{"unit_id": "q1", "question_no": 1, "kind": "long", "text": text, "fields": [{}], "filled": False}]})
            self.assertEqual(assessment["execution_state"], "manual_required")
            self.assertEqual(assessment["answer_state"], "empty")
            self.assertEqual(assessment["filled_units"], 0)
            self.assertIn("项目源码", assessment["requirements"])
            self.assertIn("演示视频", assessment["requirements"])
            self.assertIn("实验或实践报告", assessment["requirements"])
            self.assertIn("需准备", execution_block({"assessment": assessment}))

    def test_normal_theory_questions_and_unknown_pages_are_not_claimed_ready_to_submit(self):
        for text in ("简述操作系统的功能。", "简述源程序如何编译并提交运行。", "什么是视频压缩编码？"):
            self.assertFalse(unit_requirements({"kind": "long", "text": text}))
        self.assertEqual(assess_questions({"units": []})["execution_state"], "unchecked")
        self.assertEqual(assess_questions({"units": []})["answer_state"], "unknown")
        result = assess_questions({"question_count": 2, "units": [{"kind": "long", "text": "简述算法", "filled": True}, {"kind": "fill", "text": "填空", "filled": False}]})
        self.assertEqual(result["answer_state"], "partial")
        self.assertEqual(result["filled_units"], 1)
        self.assertNotIn("submittable", result)

    def test_assessment_round_trip_survives_collection_updates_without_fabricating_completion(self):
        with tempfile.TemporaryDirectory() as root:
            settings = Settings(data_root=Path(root)); settings.ensure_directories()
            database = Database(settings); database.initialize()
            account = database.create_account("fixture", "chaoxing", settings)
            assessment = assess_questions({"question_count": 1, "units": [{"kind": "long", "text": EXPERIMENT, "filled": False}]})
            item = {"courseId": "c", "classId": "a", "workId": "w", "title": "实验", "courseName": "课程", "status": "unfinished", "submittable": True, "taskUrl": "https://example.test/work", "assessment": assessment}
            identifier = database.upsert_assignment(account["id"], "chaoxing", item, settings)
            self.assertEqual(database.get_assignment(identifier)["assessment"], assessment)
            self.assertEqual(database.list_assignments(account["id"])[0]["assessment"]["answer_state"], "empty")
            item.pop("assessment")
            database.upsert_assignment(account["id"], "chaoxing", item, settings)
            self.assertEqual(database.get_assignment(identifier)["assessment"], assessment)
            self.assertTrue(database.get_assignment(identifier)["submittable"])

    async def test_blank_and_filled_controls_are_read_without_writing_any_answers(self):
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, executable_path=str(chrome_executable()))
            try:
                page = await browser.new_page()
                await page.set_content('<div class="questionLi" id="question1"><h3><span class="colorShallow">论述题</span>简述算法</h3><textarea></textarea></div><div class="questionLi" id="question2"><h3><span class="colorShallow">单选题</span>选择正确答案</h3><div role="radio" aria-label="A" aria-checked="false">A</div><input type="hidden" id="answer2" value=""></div>')
                blank = await extract_questions(page)
                self.assertEqual(assess_questions(blank)["answer_state"], "empty")
                self.assertEqual(await page.locator("textarea").input_value(), "")
                await page.locator("textarea").fill("一个有限的步骤集合")
                partial = await extract_questions(page)
                self.assertEqual(assess_questions(partial)["answer_state"], "partial")
                self.assertEqual(assess_questions(partial)["filled_units"], 1)
                self.assertEqual(await page.locator('[role="radio"]').get_attribute("aria-checked"), "false")
            finally: await browser.close()

    async def test_material_tasks_stop_before_calling_the_model_or_writing_text(self):
        with tempfile.TemporaryDirectory() as root:
            settings = Settings(data_root=Path(root)); settings.ensure_directories()
            database = Database(settings); database.initialize()
            account = database.create_account("fixture", "chaoxing", settings)
            identifier = database.upsert_assignment(account["id"], "chaoxing", {"courseId": "c", "classId": "a", "workId": "w", "title": "实验", "courseName": "课程", "status": "unfinished", "submittable": True, "taskUrl": "https://example.test/mooc2/work/task"}, settings)
            job = database.create_job(account["id"], identifier, False, False)
            session = SimpleNamespace(navigate_to=AsyncMock(), get_current_page=AsyncMock(return_value=object()))
            extracted = {"question_count": 1, "unit_count": 1, "units": [{"unit_id": "q1", "question_no": 1, "kind": "long", "type": "论述题", "text": EXPERIMENT, "fields": [{}], "filled": False}]}
            with patch.object(runner, "extract_questions", AsyncMock(return_value=extracted)), patch.object(runner, "generate_answers", AsyncMock()) as generate, patch.object(runner, "fill_answers", AsyncMock()) as fill, patch.object(runner.asyncio, "sleep", AsyncMock()):
                with self.assertRaises(runner.AgentOperationError) as outcome:
                    await runner._run_structured_chaoxing_quiz(database, job, session, object(), AsyncMock())
                self.assertEqual(outcome.exception.job_status, "incomplete")
                self.assertIn("项目源码", outcome.exception.reason)
                generate.assert_not_awaited(); fill.assert_not_awaited()
                self.assertEqual(database.get_assignment(identifier)["assessment"]["execution_state"], "manual_required")

    async def test_api_creation_retry_and_continue_respect_material_requirements(self):
        from app import main
        from fastapi import HTTPException
        with tempfile.TemporaryDirectory() as root:
            settings = Settings(data_root=Path(root), openai_api_key="fixture"); settings.ensure_directories()
            database = Database(settings); database.initialize()
            account = database.create_account("fixture", "chaoxing", settings)
            database.update_account(account["id"], status="ready")
            assessment = assess_questions({"question_count": 1, "units": [{"kind": "long", "text": EXPERIMENT, "filled": False}]})
            identifier = database.upsert_assignment(account["id"], "chaoxing", {"courseId": "c", "classId": "a", "workId": "w", "title": "实验", "courseName": "课程", "status": "unfinished", "submittable": True, "taskUrl": "https://example.test/work", "assessment": assessment}, settings)
            job = database.create_job(account["id"], identifier, False)
            database.transition_job(job["id"], "interrupted")
            with patch.object(main, "database", database), patch.object(main, "effective_settings", return_value=settings), patch.object(main, "start_job") as start:
                for operation in (lambda: main.create_job(main.CreateJobRequest(assignment_id=identifier)), lambda: main.retry_agent_job(job["id"]), lambda: main.continue_agent_job(job["id"])):
                    with self.assertRaises(HTTPException) as response: await operation()
                    self.assertEqual(response.exception.status_code, 409)
                    self.assertIn("项目源码", response.exception.detail)
                start.assert_not_called()
