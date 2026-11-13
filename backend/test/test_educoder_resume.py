import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import test_educoder as fixtures
from test_educoder_execution import completion
from app import main
from app.events import EventHub
from app.preferences import load_preferences
from app.services.tasks import runner as agent_runner
from app.services.educoder import store
from app.services.educoder.contracts import ContractError
from app.services.educoder.client import EduCoderClient
from app.services.educoder.executor import ExperimentRuntime, ExperimentError, execute_experiment
from app.services.educoder.evaluation import classify_evaluation


class ResumeRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_attached_client_initializes_auth_without_navigating_the_task_page(self):
        auth=SimpleNamespace(goto=AsyncMock(),close=AsyncMock())
        context=SimpleNamespace(new_page=AsyncMock(return_value=auth),on=Mock(),remove_listener=Mock())
        task=SimpleNamespace(context=context,goto=AsyncMock())
        client=EduCoderClient({},page=task); client.header_ready.set()
        async with client:
            self.assertIs(client.page,task)
        task.goto.assert_not_awaited(); auth.goto.assert_awaited_once(); auth.close.assert_awaited_once()

    def runtime(self, url='https://www.educoder.net/tasks/COURSE/123/two'):
        data={'homework_common_id':123,'myshixun':{'identifier':'instance'},'game':{'id':2,'status':0},'challenge':{'path':'Main.java'}}
        client=SimpleNamespace(request=AsyncMock(return_value=data))
        page=SimpleNamespace(url=url,goto=AsyncMock(),reload=AsyncMock())
        runtime=ExperimentRuntime(client,page,'123','COURSE'); runtime.adopt_editor=AsyncMock()
        return runtime,page

    async def test_resume_keeps_current_task_and_instance_without_goto_or_reload(self):
        runtime,page=self.runtime()
        await runtime.resume('instance')
        self.assertEqual(runtime.task_id,'two'); self.assertEqual(runtime.instance_id,'instance')
        page.goto.assert_not_awaited(); page.reload.assert_not_awaited(); runtime.adopt_editor.assert_awaited_once()

    async def test_foreign_assignment_or_changed_instance_is_not_replaced_by_a_navigation(self):
        runtime,page=self.runtime('https://www.educoder.net/tasks/OTHER/123/two')
        with self.assertRaises(ContractError): await runtime.resume('instance')
        page.goto.assert_not_awaited(); runtime.client.request.assert_not_awaited()
        runtime,page=self.runtime()
        with self.assertRaisesRegex(ExperimentError,'实例已变化'): await runtime.resume('other-instance')
        runtime.adopt_editor.assert_not_awaited()

    async def test_selecting_the_current_stage_does_not_reload_the_editor(self):
        runtime,page=self.runtime(); runtime.instance_id='instance'
        await runtime.select('two')
        page.goto.assert_not_awaited(); page.reload.assert_not_awaited()

    def editor_runtime(self, content, original='class Main {\n// Begin\n\n// End\n}\n'):
        area=SimpleNamespace(evaluate=AsyncMock(return_value={'accepted':True,'content':content}))
        editor=SimpleNamespace(wait_for=AsyncMock(),get_attribute=AsyncMock(return_value='inmemory://model/1'),
            click=AsyncMock(),evaluate=AsyncMock(return_value=True),locator=lambda *args:area)
        page=SimpleNamespace(locator=lambda *args:SimpleNamespace(first=editor),keyboard=SimpleNamespace(press=AsyncMock()),goto=AsyncMock(),reload=AsyncMock())
        runtime=ExperimentRuntime(None,page,'123','COURSE'); runtime.instance_id='instance'; runtime.task_id='two'
        runtime.read=AsyncMock(return_value=original); runtime.write=AsyncMock()
        return runtime,page,area

    async def test_unsaved_human_code_is_captured_from_the_full_template_before_continuing(self):
        text='class Main {\n// Begin\nint answer = 1;\n// End\n}\n'
        runtime,page,area=self.editor_runtime(text.replace('\n','\r\n'))
        await runtime.adopt_editor(['Main.java'],{'game':{'id':2},'challenge':{'path':'Main.java'}})
        self.assertEqual(runtime.write.await_args.args[2],text)
        self.assertEqual(runtime.expected_files['Main.java'],text)
        self.assertIn("ClipboardEvent('copy'",area.evaluate.await_args.args[0])
        self.assertNotIn('Control+C',[call.args[0] for call in page.keyboard.press.await_args_list])
        page.reload.assert_not_awaited(); page.goto.assert_not_awaited()

    async def test_unidentified_editor_never_overwrites_an_authorized_file(self):
        runtime,page,_=self.editor_runtime('class Other {}')
        with self.assertRaises(ExperimentError) as failure: await runtime.adopt_editor(['Main.java'],{})
        self.assertEqual(failure.exception.category,'editor_identity'); runtime.write.assert_not_awaited(); page.reload.assert_not_awaited()

    async def test_a_preexisting_pending_evaluation_is_only_polled(self):
        button=SimpleNamespace(wait_for=AsyncMock(),click=AsyncMock())
        page=SimpleNamespace(on=lambda *args:None,remove_listener=lambda *args:None,get_by_role=lambda *args,**kwargs:button)
        runtime=ExperimentRuntime(None,page,'123','COURSE'); runtime.task_id='two'
        runtime.task=AsyncMock(side_effect=[{'in_review':True,'game':{'status':0,'evaluate_count':3}},
            {'in_review':False,'sets_error_count':1,'last_compile_output':'javac error: cannot find symbol','game':{'status':0,'evaluate_count':3}}])
        report=await runtime.evaluate()
        self.assertEqual(report.kind,'compile'); self.assertTrue(report.repairable); button.click.assert_not_awaited()

    async def test_a_passed_stage_is_never_evaluated_again_even_if_the_page_just_finished(self):
        page=SimpleNamespace(get_by_role=Mock(side_effect=AssertionError('must not click')))
        runtime=ExperimentRuntime(None,page,'123','COURSE'); runtime.task_id='two'
        runtime.task=AsyncMock(return_value={'game':{'status':2,'evaluate_count':1}})
        self.assertEqual((await runtime.evaluate()).status,'passed'); page.get_by_role.assert_not_called()

    async def test_changed_saved_code_blocks_evaluation_without_refreshing_or_submitting(self):
        page=SimpleNamespace(on=lambda *args:None,remove_listener=lambda *args:None,get_by_role=Mock(),reload=AsyncMock())
        runtime=ExperimentRuntime(None,page,'123','COURSE'); runtime.task_id='two'; runtime.expected_files={'Main.java':'expected'}
        runtime.task=AsyncMock(return_value={'game':{'status':0,'evaluate_count':0}}); runtime.read=AsyncMock(return_value='human edit')
        with self.assertRaises(ExperimentError) as failure: await runtime.evaluate()
        self.assertEqual(failure.exception.category,'concurrent_edit'); self.assertFalse(runtime.evaluation_sent)
        page.get_by_role.assert_not_called(); page.reload.assert_not_awaited()


class LoopPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.case=fixtures.EducoderTests(); await self.case.asyncSetUp()

    async def asyncTearDown(self): await self.case.asyncTearDown()

    async def test_new_code_job_evaluates_by_default_independent_of_chaoxing_submission(self):
        case=self.case
        self.assertFalse(load_preferences(case.db).assignments.auto_submit)
        self.assertTrue(load_preferences(case.db).assignments.educoder_auto_evaluate)
        with (patch.object(main,'database',case.db), patch.object(main,'effective_settings',return_value=SimpleNamespace(openai_api_key='fixture')),
             patch.object(main,'platform_login_in_progress',return_value=False), patch.object(main,'start_job')):
            result=await main.enqueue_assignment(main.CreateJobRequest(assignment_id=case.assignment,auto_submit=False))
        self.assertEqual(result['auto_submit'],1)

    async def test_explicit_save_only_mode_is_still_available(self):
        case=self.case
        with (patch.object(main,'database',case.db), patch.object(main,'effective_settings',return_value=SimpleNamespace(openai_api_key='fixture')),
             patch.object(main,'platform_login_in_progress',return_value=False), patch.object(main,'start_job')):
            result=await main.enqueue_assignment(main.CreateJobRequest(assignment_id=case.assignment,auto_evaluate=False))
        self.assertEqual(result['auto_submit'],0)

    async def test_retry_uses_current_educoder_default_instead_of_the_old_save_only_bit(self):
        case=self.case; job=case.db.create_job(case.account['id'],case.assignment,False)
        case.db.transition_job(job['id'],'running'); case.db.transition_job(job['id'],'incomplete')
        with (patch.object(main,'database',case.db), patch.object(main,'effective_settings',return_value=SimpleNamespace(openai_api_key='fixture')),
             patch.object(main,'platform_login_in_progress',return_value=False), patch.object(main,'start_job')):
            result=await main.retry_agent_job(job['id'])
        self.assertEqual(result['auto_submit'],1); self.assertEqual(result['attempt'],2)

    async def test_unknown_previous_evaluation_blocks_continue_before_changing_permissions(self):
        case=self.case; job=case.db.create_job(case.account['id'],case.assignment,False)
        case.db.transition_job(job['id'],'running')
        case.db.transition_job(job['id'],'incomplete',submission_status='submission_unknown',browser_retained=1)
        with patch.object(agent_runner,'start_job') as start:
            with self.assertRaisesRegex(RuntimeError,'核对上次提交结果'):
                await agent_runner.continue_job_intervention(case.db,EventHub(),case.settings,job['id'])
        start.assert_not_called()
        current=case.db.get_job(job['id']); self.assertEqual(current['auto_submit'],0); self.assertEqual(current['status'],'incomplete')

    async def test_continuing_an_old_manual_job_records_the_new_evaluation_permission(self):
        case=self.case; job=case.db.create_job(case.account['id'],case.assignment,False)
        case.db.transition_job(job['id'],'running'); case.db.transition_job(job['id'],'needs_intervention',submission_status='filled_pending_review',browser_retained=1)
        session=SimpleNamespace(kill=AsyncMock(),get_current_page_url=AsyncMock(return_value=job['task_url']))
        lock=asyncio.Lock(); await lock.acquire()
        retained=agent_runner.PreservedSession(session,None,lock)
        agent_runner._intervention_sessions[job['id']]=retained; agent_runner._job_sessions[job['id']]=session
        try:
            with patch.object(agent_runner,'start_job') as start:
                result=await agent_runner.continue_job_intervention(case.db,EventHub(),case.settings,job['id'])
            self.assertEqual(result['auto_submit'],1); self.assertIs(start.call_args.kwargs['reused_session'],retained)
            self.assertTrue(any(event['kind']=='evaluation_mode' for event in case.db.list_events(job['id'])))
            session.kill.assert_not_awaited()
        finally:
            agent_runner._intervention_sessions.pop(job['id'],None); agent_runner._job_sessions.pop(job['id'],None)
            if lock.locked(): lock.release()

    async def test_resume_uses_the_current_unfinished_stage_and_does_not_open_the_entry(self):
        case=self.case; job=case.db.create_job(case.account['id'],case.assignment,True); case.db.transition_job(job['id'],'running')
        runtime=fixtures.FakeRuntime(); runtime.task_id='two'; runtime.passed={'one'}
        runtime.resume=AsyncMock(); runtime.task=AsyncMock(return_value={'game':{'id':'two','status':0},'challenge':{'description':'Fill template','path':'Main.java'}})
        runtime.select=AsyncMock(side_effect=AssertionError('must keep the current editor'))
        store.claim_instance(case.db,case.account['id'],runtime.instance_id,job['id'])
        model=SimpleNamespace(ainvoke=AsyncMock(return_value=completion()))
        result=await execute_experiment(case.db,case.db.get_job(job['id']),None,model,AsyncMock(),agent_runner.JobControl(),
            case.db.diagnostics.trace('assignment',job['id']),runtime=runtime,resume=True)
        self.assertEqual(result['status'],'completed'); self.assertEqual(runtime.evaluations,1)
        runtime.open.assert_not_awaited(); runtime.select.assert_not_awaited(); runtime.refresh.assert_not_awaited()
        runtime.resume.assert_awaited_once_with('student-instance')

    async def test_runtime_error_is_repaired_and_then_moves_to_the_next_stage(self):
        class Runtime(fixtures.FakeRuntime):
            async def evaluate(self):
                self.evaluations+=1
                if self.evaluations==1: return classify_evaluation({'status':0,'last_compile_output':'Exception in thread main java.lang.NullPointerException'})
                self.passed.add(self.task_id)
        runtime=Runtime(); model=SimpleNamespace(ainvoke=AsyncMock(side_effect=[completion('first'),completion('fixed'),completion('second')]))
        result,_,_=await self.case.run_experiment(True,runtime,model)
        self.assertEqual(result['status'],'completed'); self.assertEqual(runtime.evaluations,3)
        self.assertIn('NullPointerException',str(model.ainvoke.await_args_list[1].args[0]))
        self.assertIn('不新增注释',str(model.ainvoke.await_args_list[0].args[0]))
        self.assertIn('original one',str(model.ainvoke.await_args_list[0].args[0]))

    async def test_an_existing_pending_evaluation_finishes_before_any_model_write(self):
        from app.services.educoder.evaluation import EvaluationReport
        class Runtime(fixtures.FakeRuntime):
            async def select(self,identifier):
                data=await super().select(identifier)
                if identifier=='one': data['in_review']=True
                return data
            async def evaluate(self):
                self.evaluations+=1; self.passed.add(self.task_id)
                return EvaluationReport('passed','verified_stage','passed')
        runtime=Runtime(); model=SimpleNamespace(ainvoke=AsyncMock(return_value=completion('second stage code')))
        result,_,_=await self.case.run_experiment(True,runtime,model)
        self.assertEqual(result['status'],'completed'); self.assertEqual(model.ainvoke.await_count,1); self.assertEqual(runtime.writes,1)

    async def test_model_receives_full_placeholder_template_and_returns_only_its_editable_code(self):
        original='class Main {\n// Begin\n\n// End\n}\n'
        answer=original.replace('// Begin\n\n','// Begin\nint value = 1;\n')
        runtime=fixtures.FakeRuntime(); runtime.files['one']=original; runtime.passed={'two'}
        model=SimpleNamespace(ainvoke=AsyncMock(return_value=completion(answer)))
        result,_,_=await self.case.run_experiment(True,runtime,model)
        self.assertEqual(result['status'],'completed'); self.assertEqual(runtime.files['one'],answer)
        request=str(model.ainvoke.await_args.args[0])
        self.assertIn('// Begin',request); self.assertIn('files[].content',request); self.assertIn('不新增注释',request)
