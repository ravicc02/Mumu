import json
import unittest

from playwright.async_api import async_playwright

from app.services.runtime.browser import chrome_executable
from app.services.assignments.quiz_adapter import EXTRACT_QUESTIONS_SCRIPT, FILL_ANSWERS_SCRIPT


class QuizSelectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_combination_multiple_choice_uses_native_checked_class_and_is_idempotent(self):
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, executable_path=str(chrome_executable()))
            try:
                page = await browser.new_page()
                choices = ''.join(f'<div onclick="choose(this)"><span class="num_option_dx" data="{key}">{key}</span></div>' for key in 'ABCD')
                await page.set_content(f'''<div class="questionLi" id="question1"><h3><span class="colorShallow">组合题</span></h3>
                <div class="reading_answer" data="1part"><span class="read_type">多选题</span><input type="hidden" id="answer1part"><div class="stem_answer">{choices}</div></div></div>
                <script>window.clicks=0;function choose(root){{window.clicks++;const option=root.querySelector('span');option.classList.toggle('check_answer_dx');root.setAttribute('aria-checked',String(option.classList.contains('check_answer_dx')));document.querySelector('#answer1part').value=[...document.querySelectorAll('.check_answer_dx')].map(node=>node.getAttribute('data')).join('');}}</script>''')
                await page.evaluate(EXTRACT_QUESTIONS_SCRIPT)
                async def fill(values):
                    return json.loads(await page.evaluate(FILL_ANSWERS_SCRIPT, json.dumps({'answers': [{'unit_id': 'q1:r0', 'values': values}]})))[0]
                first = await fill(['A', 'B', 'C'])
                self.assertEqual(first['checked'], ['A', 'B', 'C'])
                self.assertEqual(first['hidden_value'], 'ABC')
                again = await fill(['A', 'B', 'C'])
                self.assertEqual(again['checked'], ['A', 'B', 'C'])
                self.assertEqual(await page.evaluate('window.clicks'), 3)
                changed = await fill(['A', 'C'])
                self.assertEqual(changed['checked'], ['A', 'C'])
                self.assertEqual(changed['hidden_value'], 'AC')
                self.assertEqual(await page.evaluate('window.clicks'), 4)
            finally: await browser.close()
