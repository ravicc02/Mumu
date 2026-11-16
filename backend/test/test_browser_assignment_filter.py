from __future__ import annotations

import unittest

from playwright.async_api import async_playwright

from app.services.assignments.quiz_adapter import extract_questions, fill_answers, QuizAnswerSheet
from app.services.assignments.readiness import assess_questions, direct_answerable, unit_requirements
from app.services.runtime.browser import chrome_executable


class BrowserAssignmentFilterTests(unittest.IsolatedAsyncioTestCase):
    def test_material_and_theory_phrases_do_not_share_the_same_result(self):
        material = [
            "请制作一份 PPT，用于下周汇报。",
            "请制作PPT。",
            "写一份关于软件工程的报告。",
            "请准备小组汇报。",
            "请撰写一份调查报告。",
            "完成实验报告并提交。",
            "上传附件。",
            "请完成项目，提交源程序代码和压缩包。",
        ]
        theory = [
            "简述实验报告应包含哪些部分。",
            "什么是源代码？",
            "解释 PPT 中母版的作用。",
            "说明如何压缩视频文件。",
            "简述源程序如何编译并提交运行。",
            "上传文件有几种方式？",
            "为什么要提交实验报告？",
        ]
        for text in material:
            with self.subTest(text=text):
                self.assertTrue(unit_requirements({"text": text, "fields": [{}]}))
        for text in theory:
            with self.subTest(text=text):
                self.assertFalse(unit_requirements({"text": text, "fields": [{}]}))

    async def test_real_dom_filter_handles_mixed_questions_notifications_and_uploads(self):
        choice = '<div class="questionLi" id="question1"><h3><span class="colorShallow">单选题</span>下列哪项是操作系统？</h3><label><input type="radio" name="q1" value="A">A. Windows</label><label><input type="radio" name="q1" value="B">B. Word</label></div>'
        def essay(text, extra=""):
            return '<div class="questionLi" id="question3"><h3><span class="colorShallow">简答题</span>' + text + '</h3><textarea></textarea>' + extra + '</div>'
        mixed = choice + '<div class="questionLi" id="question2"><h3>填空题：二进制有几个数字？</h3><input type="text"></div>' + essay("简述进程与线程的区别。")
        cases = [
            ("混合选择填空简答", mixed, True),
            ("理论中的报告", essay("简述实验报告应包含哪些部分。"), True),
            ("理论中的代码", essay("阅读代码 print(1+1)，说明输出结果。"), True),
            ("只含通知", essay("通知：请同学们明天下午到教室集合，收到请回复。"), False),
            ("计算机课程通知不是计算题", essay("通知：明天计算机课取消，请同学们知悉。"), False),
            ("表述不明", essay("按要求完成"), False),
            ("实验数字标题", essay("1. 实验2"), False),
            ("实验中文标题", essay("实验二"), False),
            ("序号实验标题", essay("第二次实验："), False),
            ("作业编号标题", essay("作业一"), False),
            ("标题后有具体问题", essay("实验二：观察进程状态，解释就绪与阻塞的区别。"), True),
            ("泛泛的要求", essay("请按要求完成本次作业"), False),
            ("只有题型没有题干", essay(""), False),
            ("附件中才有要求", essay("具体题目详见附件。"), False),
            ("没有题目", '<p>请下周提交本次实践报告。</p>', False),
            ("PPT 交付", essay("请制作 PPT 并完成小组汇报。"), False),
            ("综合材料夹在混合题中", mixed + '<p>作业要求：另需上传实验报告与项目源码压缩包。</p>', False),
            ("必需文件框", mixed + '<input type="file" required>', False),
            ("独立文件入口", choice + '<input type="file">', False),
            ("编辑器可选上传不影响简答", essay("简述操作系统的功能。", '<div class="edui-toolbar"><input type="file"></div>'), True),
            ("明确必填覆盖编辑器位置", essay("简述操作系统的功能。", '<div class="edui-toolbar"><input type="file" required></div>'), False),
            ("禁用的答案框", '<div class="questionLi" id="question1"><h3>填空题：计算 1+1</h3><input type="text" disabled></div>', False),
            ("禁用的选择控件", '<div class="questionLi" id="question1"><h3>单选题：下列哪项是操作系统？</h3><label><input type="radio" value="A" disabled>A. Windows</label></div>', False),
            ("选项中的材料词不影响", '<div class="questionLi" id="question1"><h3>单选题：哪个文件是演示文稿？</h3><label><input type="radio" value="A">A. 上传报告.zip</label><label><input type="radio" value="B">B. slides.pptx</label></div><button>提交</button>', True),
        ]
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, executable_path=str(chrome_executable()))
            try:
                page = await browser.new_page()
                for name, html, expected in cases:
                    with self.subTest(name=name):
                        await page.set_content(html)
                        extracted = await extract_questions(page)
                        assessment = assess_questions(extracted)
                        self.assertEqual(direct_answerable({"assessment": assessment}), expected, assessment)
                        self.assertEqual(await page.locator('textarea').first.input_value() if await page.locator('textarea').count() else '', '')
                await page.set_content(mixed)
                extracted = await extract_questions(page)
                self.assertTrue(assess_questions(extracted)["automation_supported"])
                sheet = QuizAnswerSheet.model_validate({"answers": [
                    {"unit_id": "q1", "values": ["A"]},
                    {"unit_id": "q2", "values": ["2"]},
                    {"unit_id": "q3", "values": ["进程拥有独立资源，线程共享进程资源。"]},
                ]})
                await fill_answers(page, sheet)
                self.assertEqual(await page.locator('input[type=text]').input_value(), '2')
                self.assertEqual(await page.locator('textarea').input_value(), sheet.answers[2].values[0])
            finally:
                await browser.close()

    def test_missing_one_subquestion_or_control_rejects_the_whole_assignment(self):
        units = [
            {"kind": "single", "text": "下列哪项是操作系统？", "options": [{"key": "A"}]},
            {"kind": "long", "text": "简述进程的作用", "fields": [{}]},
            {"kind": "fill", "text": "计算 1+1", "fields": []},
        ]
        self.assertFalse(assess_questions({"units": units})["automation_supported"])
        units[-1]["fields"] = [{}]
        self.assertTrue(assess_questions({"units": units})["automation_supported"])
        units[-1]["text"] = ""
        self.assertFalse(assess_questions({"units": units})["automation_supported"])
        self.assertFalse(direct_answerable({"assessment": {"execution_state": "direct_fill"}}))
        self.assertFalse(direct_answerable({"assessment": {"runtime": "educoder", "execution_state": "manual_required"}}))
