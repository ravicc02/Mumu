import json
import unittest

from playwright.async_api import async_playwright

from app.services.runtime.browser import chrome_executable
from app.services.assignments.quiz_adapter import EXTRACT_QUESTIONS_SCRIPT, FILL_ANSWERS_SCRIPT
from app.services.assignments.readiness import assess_questions


class QuizSelectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_shared_choices_without_stem_wrapper_are_extracted_and_filled_idempotently(self):
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, executable_path=str(chrome_executable()))
            try:
                page = await browser.new_page()
                groups = ''.join(
                    '<div><p>请选择与描述对应的术语。</p><div class="B-answerCon">'
                    + ''.join(f'<span data="{key}">{key}</span>' for key in 'ABCDEF')
                    + '</div></div>' for _ in range(4)
                )
                await page.set_content(f'''<div class="questionLi" id="question27">
                    <h3><span class="colorShallow">共用选项题</span>根据以下描述选择对应术语。</h3>
                    <input type="hidden" id="answer27">{groups}</div>
                    <script>window.clicks=0;document.querySelectorAll('.B-answerCon span').forEach(option=>option.onclick=()=>{{
                        window.clicks++;option.parentElement.querySelectorAll('span').forEach(other=>other.classList.remove('check_answer'));
                        option.classList.add('check_answer');
                    }});</script>''')
                async def extract():
                    return json.loads(await page.evaluate(EXTRACT_QUESTIONS_SCRIPT))
                extracted = await extract()
                unit = extracted['units'][0]
                self.assertEqual(unit['kind'], 'shared')
                self.assertEqual([group['options'] for group in unit['groups']], [list('ABCDEF')] * 4)
                self.assertTrue(assess_questions(extracted)['automation_supported'])
                self.assertFalse(unit['filled'])
                self.assertEqual(await page.locator('#answer27').input_value(), '')

                async def fill(values):
                    result = json.loads(await page.evaluate(FILL_ANSWERS_SCRIPT, json.dumps({'answers': [{'unit_id': 'q1', 'values': values}]})))[0]
                    self.assertEqual(json.loads(result['hidden_value']), [{'name': i+1, 'content': key} for i, key in enumerate(values)])
                    self.assertEqual(await page.locator('.B-answerCon .check_answer').all_text_contents(), values)
                await fill(list('ABCD'))
                await fill(list('ABCD'))
                self.assertEqual(await page.evaluate('window.clicks'), 4)
                self.assertTrue((await extract())['units'][0]['filled'])
                await fill(list('ABCF'))
                self.assertEqual(await page.evaluate('window.clicks'), 5)

                await page.locator('.B-answerCon').nth(3).evaluate("node=>node.setAttribute('aria-disabled','true')")
                disabled = await extract()
                self.assertEqual(disabled['units'][0]['groups'][3]['options'], [])
                self.assertFalse(assess_questions(disabled)['automation_supported'])
                await page.locator('.B-answerCon').nth(3).evaluate("node=>{node.removeAttribute('aria-disabled');node.innerHTML=''}")
                self.assertFalse(assess_questions(await extract())['automation_supported'])
            finally:
                await browser.close()

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
