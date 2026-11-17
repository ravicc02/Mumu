import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image
from io import BytesIO
from playwright.async_api import async_playwright

import test_educoder as fixtures
from app.services.educoder.desktop import CanvasDriver, DesktopAction, DesktopFrame, execute_desktop_stage
from app.services.educoder.executor import CodeFile, CodePatch, ExperimentError, execute_experiment
from app.services.educoder.code_contract import CodeContractError, validate_patch
from app.services.educoder.evaluation import EvaluationReport
from app.services.runtime.browser import chrome_executable
from app.services.tasks.runner import JobControl
from app.services.tasks.spec import educoder_task_spec


class CanvasDriverTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(executable_path=str(chrome_executable()), headless=True)
        self.page = await self.browser.new_page(viewport={"width":1200, "height":800})
        html = '''<input id="outside"><canvas width="640" height="400" tabindex="0"></canvas>
        <script>
        const canvas=document.querySelector('canvas');
        canvas.getContext('2d').fillRect(0,0,640,400);
        window.keys=[]; window.pointer=[];
        canvas.addEventListener('keydown',e=>keys.push(e.key));
        canvas.addEventListener('mousedown',e=>pointer.push([e.offsetX,e.offsetY,e.button]));
        </script>'''
        await self.page.route('https://www.educoder.net/**', lambda route: route.fulfill(body=html,content_type='text/html'))
        await self.page.goto('https://www.educoder.net/tasks/CLASS/123/one')
        self.runtime = SimpleNamespace(page=self.page, route_id='CLASS', work_id='123', task_id='one', instance_id='instance', task=AsyncMock())
        self.driver = CanvasDriver(self.runtime)

    async def asyncTearDown(self):
        await self.browser.close()
        await self.playwright.stop()

    async def test_observe_pointer_keyboard_drag_and_focus_use_the_same_canvas(self):
        frame = await self.driver.observe()
        self.assertEqual(Image.open(BytesIO(frame.image)).size, (640,400))
        await self.driver.perform(DesktopAction(action='double_click',summary='Open',x=500,y=500,wait_seconds=1),frame)
        self.assertEqual(await self.page.evaluate('pointer.length'),2)
        self.assertEqual(await self.page.evaluate('pointer[0]'),[320,200,0])
        await self.page.locator('#outside').focus()
        await self.driver.perform(DesktopAction(action='type',summary='Type',text='echo 1',wait_seconds=1),await self.driver.observe())
        self.assertEqual(await self.page.locator('#outside').input_value(),'')
        self.assertEqual(await self.page.evaluate("keys.join('')"),'echo 1')
        await self.driver.perform(DesktopAction(action='press',summary='Run',key='Enter',wait_seconds=1),await self.driver.observe())
        self.assertEqual(await self.page.evaluate('keys.at(-1)'),'Enter')
        await self.driver.perform(DesktopAction(action='drag',summary='Move',x=100,y=100,end_x=800,end_y=800,wait_seconds=1),await self.driver.observe())

    async def test_old_geometry_replaced_canvas_and_foreign_page_reject_input(self):
        frame = await self.driver.observe()
        await self.page.locator('canvas').evaluate("element=>element.style.marginLeft='30px'")
        with self.assertRaisesRegex(ExperimentError,'旧坐标'):
            await self.driver.perform(DesktopAction(action='click',summary='Click',x=500,y=500),frame)
        frame = await self.driver.observe()
        await self.page.locator('canvas').evaluate('element=>element.replaceWith(element.cloneNode(true))')
        with self.assertRaisesRegex(ExperimentError,'旧坐标'):
            await self.driver.perform(DesktopAction(action='click',summary='Click',x=500,y=500),frame)
        await self.page.goto('https://www.educoder.net/tasks/OTHER/123/one')
        with self.assertRaisesRegex(ExperimentError,'离开本任务'):
            await self.driver.observe()

    async def test_iframe_canvas_is_observed_and_ambiguous_canvases_are_rejected(self):
        await self.page.set_content('<iframe srcdoc="&lt;canvas width=640 height=400 tabindex=0&gt;&lt;/canvas&gt;" width="800" height="500"></iframe>')
        await self.page.frames[1].locator('canvas').wait_for()
        frame = await self.driver.observe()
        self.assertEqual(Image.open(BytesIO(frame.image)).size,(640,400))
        await self.page.evaluate("document.body.insertAdjacentHTML('beforeend','<canvas width=640 height=400></canvas>')")
        with self.assertRaisesRegex(ExperimentError,'唯一'):
            await self.driver.observe()


class PracticalContractTests(unittest.TestCase):
    def test_notebook_protects_markdown_output_metadata_and_readonly_cells(self):
        original = {'nbformat':4,'nbformat_minor':5,'metadata':{},'cells':[
            {'cell_type':'markdown','metadata':{},'source':['Instructions']},
            {'cell_type':'code','metadata':{},'source':['# Begin\n','# End\n'],'outputs':[],'execution_count':None},
            {'cell_type':'code','metadata':{'editable':False},'source':['fixed=1'],'outputs':[],'execution_count':None}]}
        changed = json.loads(json.dumps(original))
        changed['cells'][1]['source']=['# Begin\n','answer=42\n','# End\n']
        def validate(value):
            return validate_patch({'work.ipynb':json.dumps(original)},CodePatch(summary='Fill',files=[CodeFile(path='work.ipynb',content=json.dumps(value))]))
        self.assertIn('answer=42',validate(changed)['work.ipynb'])
        for cell, field, value in [(0,'source',['rewritten']), (1,'outputs',[{'text':'fake'}]), (1,'execution_count',9), (2,'source',['fixed=2'])]:
            invalid=json.loads(json.dumps(changed))
            invalid['cells'][cell][field]=value
            with self.subTest(cell=cell,field=field), self.assertRaises(CodeContractError):
                validate(invalid)

    def test_nonjava_markers_and_desktop_input_contract_are_enforced(self):
        original='fixed=1\n# Begin\n# End\n'
        patch=CodePatch(summary='Fill',files=[CodeFile(path='main.py',content=original.replace('fixed=1','fixed=2'))])
        with self.assertRaises(CodeContractError):
            validate_patch({'main.py':original},patch)
        for data in [{'action':'click'}, {'action':'type','text':'中文'}, {'action':'press','key':'Control+W'}]:
            with self.subTest(data=data),self.assertRaises(ValueError):
                DesktopAction(summary='Step',**data)


class DesktopExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.case=fixtures.EducoderTests()
        await self.case.asyncSetUp()
        self.job=self.case.db.create_job(self.case.account['id'],self.case.assignment,True)
        self.runtime=SimpleNamespace(instance_id='student-instance', task_id='one', evaluation_sent=False,
            evaluate=AsyncMock(return_value=EvaluationReport('passed','verified_stage','Passed')))
        self.frame=DesktopFrame(b'png',{'x':0,'y':0,'width':640,'height':400},asyncio.get_running_loop().time(),'one','student-instance')
        self.driver=SimpleNamespace(observe=AsyncMock(return_value=self.frame),perform=AsyncMock())
        self.model=SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(completion=DesktopAction(action='evaluate',summary='Verify'))))
        self.spec=educoder_task_spec({'shixun':{'vnc':True}},instance_id=self.runtime.instance_id,challenge_id='one',paths=[],auto_submit=True,has_canvas=True)

    async def asyncTearDown(self):
        await self.case.asyncTearDown()

    async def run_stage(self, data=None, spec=None):
        return await execute_desktop_stage(self.case.db,self.job,self.runtime,self.model,AsyncMock(),JobControl(),
            self.case.db.diagnostics.trace('assignment',self.job['id']),data or {'challenge':{'description':'Create requested file'}},{},2,0,
            spec=spec or self.spec,driver=self.driver)

    async def test_unknown_evaluation_never_repeats_or_claims_completion(self):
        self.runtime.evaluate.return_value=EvaluationReport('unknown','unknown','No receipt')
        with self.assertRaises(ExperimentError) as failure:
            await self.run_stage()
        self.assertTrue(failure.exception.unknown)
        self.runtime.evaluate.assert_awaited_once()
        self.assertEqual(self.case.db.get_job(self.job['id'])['submission_status'],'submission_unknown')

    async def test_existing_evaluation_is_queried_before_a_model_action(self):
        result=await self.run_stage({'in_review':True,'challenge':{'description':'Create file'}})
        self.assertIsNone(result)
        self.model.ainvoke.assert_not_awaited()
        self.runtime.evaluate.assert_awaited_once()

    async def test_manual_checkpoint_keeps_used_budget_on_continue(self):
        self.case.db.save_task_checkpoint(attempt_id=self.job['id'],attempt_kind='assignment',phase='executing',
            goal={'instance_id':self.runtime.instance_id,'challenge_id':'one'},completed={'step':60,'evaluations':1})
        with self.assertRaisesRegex(ExperimentError,'操作上限'):
            await self.run_stage()
        self.model.ainvoke.assert_not_awaited()

    async def test_mixed_desktop_and_python_stages_require_final_classroom_receipt(self):
        runtime=fixtures.FakeRuntime()
        original_select=runtime.select
        async def select(identifier):
            data=await original_select(identifier)
            data['challenge']['path']='' if identifier=='one' else 'main.py'
            data['shixun']={'vnc':identifier=='one'}
            return data
        runtime.select=select
        runtime.page=SimpleNamespace(locator=lambda selector:SimpleNamespace(first=SimpleNamespace(is_visible=AsyncMock(return_value=True)),count=AsyncMock(return_value=1)))
        runtime.page.frames=[runtime.page]
        runtime.adopt_editor=AsyncMock()
        evaluate=runtime.evaluate
        async def evaluated():
            await evaluate()
            return EvaluationReport('passed','verified_stage','Passed')
        runtime.evaluate=evaluated
        code=SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(completion=CodePatch(summary='Implement',files=[CodeFile(path='main.py',content='answer=42')]))))
        with patch('app.services.educoder.desktop.CanvasDriver',return_value=self.driver):
            result=await execute_experiment(self.case.db,self.job,None,code,AsyncMock(),JobControl(),
                self.case.db.diagnostics.trace('assignment',self.job['id']),runtime=runtime,desktop_llm_factory=lambda:self.model)
        self.assertEqual(result['status'],'completed')
        self.assertEqual(runtime.evaluations,2)
        self.assertEqual(runtime.writes,1)
        self.assertEqual(code.ainvoke.await_count,1)
        self.assertEqual(self.model.ainvoke.await_count,1)
