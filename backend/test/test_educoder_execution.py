from __future__ import annotations

import unittest
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import test_educoder as fixtures
from app.services.educoder.executor import CodeFile, CodePatch, ExperimentError, ExperimentRuntime, challenge_list
from app.services.educoder.code_contract import CodeContractError, validate_patch
from app.services.educoder.evaluation import EvaluationReport, classify_evaluation
from app.services.task_spec import educoder_task_spec
from app.services.educoder.client import EduCoderClient


def completion(text="updated source"):
    return SimpleNamespace(completion=CodePatch(summary="Implement requested behavior", files=[CodeFile(path="Main.java", content=text)]))


class CodeContractTests(unittest.TestCase):
    def test_suite_annotations_can_replace_blank_line_before_class(self):
        original = "package step3;\n\nimport org.junit.runner.RunWith;\nimport org.junit.runners.Suite;\n\n//******\n\npublic class SuiteTest {\n}\n"
        changed = original.replace("//******\n\n", "//******\n@RunWith(Suite.class)\n@Suite.SuiteClasses({CalculateTest.class, CarTest.class})\n")
        patch = CodePatch(summary="Complete suite annotations", files=[CodeFile(path="SuiteTest.java", content=changed)])
        self.assertEqual(validate_patch({"SuiteTest.java": original}, patch)["SuiteTest.java"], changed)

    def test_declaration_indentation_does_not_change_protected_identity(self):
        original = "\npackage sample;\n\nimport org.junit.Test;\n\npublic class Main {}\n"
        changed = "package sample;\nimport org.junit.Test;\n  public class Main {}\n"
        self.assertEqual(validate_patch({"Main.java": original}, completion(changed).completion)["Main.java"], changed)

    def test_declaration_changes_still_reject_and_identify_the_file(self):
        original = "package sample;\nimport org.junit.Test;\npublic class Main {}\n"
        for old, new in (("sample", "other"), ("org.junit.Test", "other.Test"), ("class Main", "class Other")):
            with self.subTest(declaration=old):
                with self.assertRaises(CodeContractError) as failure:
                    validate_patch({"Main.java": original}, completion(original.replace(old, new)).completion)
                self.assertEqual(failure.exception.details["path"], "Main.java")
                self.assertEqual(failure.exception.details["rule"], "java_declarations")
                self.assertNotEqual(failure.exception.details["expected"], failure.exception.details["actual"])

    def test_editable_regions_preserve_every_protected_byte(self):
        original = "package sample;\nclass Main {\n// Begin\n\n// End\n}\n"
        changed = original.replace("// Begin\n\n", "// Begin\n  int value = 1;\n")
        self.assertEqual(validate_patch({"Main.java":original}, completion(changed).completion)["Main.java"],changed)
        with self.assertRaises(CodeContractError): validate_patch({"Main.java":original}, completion(changed.replace("package sample", "package other")).completion)

    def test_unknown_files_and_changed_declarations_fail_before_writes(self):
        with self.assertRaises(CodeContractError): validate_patch({"Other.java":"class Other {}"}, completion().completion)
        with self.assertRaises(CodeContractError): validate_patch({"Main.java":"package a;\nclass Main {}"}, completion("package b;\nclass Main {}").completion)

    def test_real_locked_stages_keep_position_without_inventing_identifiers(self):
        records = challenge_list([{"identifier":"first","position":1,"status":0},{"identifier":None,"position":2,"status":3}])
        self.assertIsNone(records[1]["identifier"])
        with self.assertRaises(Exception): challenge_list([{"identifier":None,"position":2,"status":2}])

    def test_task_contract_routes_canvas_and_unsupported_files_without_execution_permission(self):
        code = educoder_task_spec({},instance_id="instance",challenge_id="task",paths=["Main.java"],auto_submit=True)
        self.assertEqual(code.route,"code"); self.assertTrue(code.permissions.evaluate)
        manual = educoder_task_spec({},instance_id="instance",challenge_id="task",paths=["Main.java"],auto_submit=False)
        self.assertFalse(manual.permissions.evaluate)
        desktop = educoder_task_spec({},instance_id="instance",challenge_id="task",paths=[],auto_submit=True,has_editor=False,has_canvas=True)
        self.assertEqual(desktop.route,"desktop"); self.assertFalse(desktop.permissions.save)
        unknown = educoder_task_spec({},instance_id="instance",challenge_id="task",paths=["main.py"],auto_submit=True)
        self.assertEqual(unknown.route,"unsupported"); self.assertTrue(unknown.blocker)

    def test_results_distinguish_pending_compile_test_environment_and_pass(self):
        self.assertEqual(classify_evaluation({"status":-1}).status,"pending")
        self.assertEqual(classify_evaluation({"status":2}).status,"passed")
        compile = classify_evaluation({"status":0,"last_compile_output":"javac error: cannot find symbol", "test_sets":[{"compile_success":False}]})
        self.assertTrue(compile.repairable); self.assertEqual(compile.kind,"compile")
        assertion = classify_evaluation({"status":0,"test_sets":[{"compile_success":True,"result":False,"actual_output":"hidden","actual_output_visible":False,"input":"secret-input","output":"secret-answer"}]})
        self.assertEqual(assertion.kind,"test"); self.assertTrue(assertion.repairable)
        self.assertNotIn("hidden",assertion.details); self.assertNotIn("secret",assertion.details)
        missing = classify_evaluation({"status":0,"last_compile_output":"package org.junit does not exist"})
        self.assertEqual(missing.kind,"environment"); self.assertFalse(missing.repairable)
        self.assertEqual(classify_evaluation({"status":True}).status,"unknown")


class CodeExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.case = fixtures.EducoderTests(); await self.case.asyncSetUp()
    async def asyncTearDown(self): await self.case.asyncTearDown()

    async def test_suite_annotation_patch_reaches_save_evaluation_and_completion(self):
        original = "package step3;\nimport org.junit.runner.RunWith;\nimport org.junit.runners.Suite;\n\npublic class Main {}\n"
        changed = original.replace("\n\npublic class", "\n@RunWith(Suite.class)\n@Suite.SuiteClasses({CalculateTest.class, CarTest.class})\npublic class")
        runtime = fixtures.FakeRuntime()
        runtime.files = dict.fromkeys(runtime.files, original)
        model = SimpleNamespace(ainvoke=AsyncMock(return_value=completion(changed)))
        result, runtime, _ = await self.case.run_experiment(True, runtime, model)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(runtime.writes, 2)
        self.assertEqual(runtime.evaluations, 2)
        self.assertTrue(all(content == changed for content in runtime.files.values()))

    async def test_rejected_declarations_return_specific_contract_to_repair_model(self):
        original = "package sample;\npublic class Main {}\n"
        runtime = fixtures.FakeRuntime()
        runtime.files = dict.fromkeys(runtime.files, original)
        model = SimpleNamespace(ainvoke=AsyncMock(side_effect=[
            completion(original.replace("sample", "other")), completion(original), completion(original)]))
        result, runtime, _ = await self.case.run_experiment(True, runtime, model)
        self.assertEqual(result["status"], "completed")
        request = json.loads(model.ainvoke.await_args_list[1].args[0][1].content)
        rejection = request["previous_failure"]
        self.assertEqual(rejection["contract"], {"path": "Main.java", "rule": "java_declarations",
            "expected": ["package sample;", "public class Main"], "actual": ["package other;", "public class Main"]})
        self.assertEqual(rejection["model_call"], 1)
        self.assertEqual(runtime.writes, 0)
        self.assertEqual(runtime.evaluations, 2)

    async def test_compile_failure_repairs_from_feedback_then_verifies_all_stages(self):
        class Runtime(fixtures.FakeRuntime):
            async def evaluate(self):
                if self.evaluations == 0:
                    self.evaluations += 1
                    return classify_evaluation({"status":0,"last_compile_output":"javac error: cannot find symbol assertEquals"})
                return await super().evaluate()
        runtime = Runtime()
        model = SimpleNamespace(ainvoke=AsyncMock(side_effect=[completion("first revision"),completion("repaired revision"),completion("second stage")]))
        result, runtime, model = await self.case.run_experiment(True,runtime,model)
        self.assertEqual(result["status"],"completed"); self.assertEqual(runtime.evaluations,3)
        self.assertIn("cannot find symbol",str(model.ainvoke.await_args_list[1].args[0]))

    async def test_same_failed_artifact_is_never_evaluated_twice(self):
        class Runtime(fixtures.FakeRuntime):
            async def evaluate(self):
                self.evaluations += 1
                return classify_evaluation({"status":0,"last_compile_output":"javac error: cannot find symbol"})
        runtime = Runtime(); model = SimpleNamespace(ainvoke=AsyncMock(return_value=completion("unchanged failing revision")))
        with self.assertRaises(ExperimentError) as failure: await self.case.run_experiment(True,runtime,model)
        self.assertEqual(failure.exception.category,"no_progress"); self.assertEqual(runtime.evaluations,1)
        self.assertEqual(model.ainvoke.await_count,2)

    async def test_unknown_evaluation_stops_without_model_or_submission_retry(self):
        class Runtime(fixtures.FakeRuntime):
            async def evaluate(self):
                self.evaluations += 1; self.evaluation_sent = True
                raise ExperimentError("unknown result",interrupted=True,unknown=True)
        runtime = Runtime(); model = SimpleNamespace(ainvoke=AsyncMock(return_value=completion()))
        with self.assertRaises(ExperimentError) as failure: await self.case.run_experiment(True,runtime,model)
        self.assertTrue(failure.exception.unknown); self.assertEqual(runtime.evaluations,1); self.assertEqual(model.ainvoke.await_count,1)
        self.assertEqual(self.case.db.list_jobs()[0]["submission_status"],"submission_unknown")

    async def test_missing_environment_does_not_trigger_code_repair(self):
        class Runtime(fixtures.FakeRuntime):
            async def evaluate(self):
                self.evaluations += 1
                return classify_evaluation({"status":0,"last_compile_output":"package org.junit does not exist"})
        runtime = Runtime(); model = SimpleNamespace(ainvoke=AsyncMock(return_value=completion()))
        with self.assertRaises(ExperimentError) as failure: await self.case.run_experiment(True,runtime,model)
        self.assertEqual(failure.exception.category,"environment"); self.assertEqual(model.ainvoke.await_count,1)

    async def test_model_protocol_has_one_bounded_repair(self):
        invalid = SimpleNamespace(completion={"unexpected":True})
        model = SimpleNamespace(ainvoke=AsyncMock(side_effect=[invalid,completion("correct schema"),completion("second stage")]))
        result, runtime, _ = await self.case.run_experiment(True,llm=model)
        self.assertEqual(result["status"],"completed"); self.assertEqual(model.ainvoke.await_count,3)
        self.assertEqual(runtime.evaluations,2)

    async def test_repeated_invalid_model_schema_never_reaches_file_writes(self):
        model = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(completion={"unexpected":True})))
        runtime = fixtures.FakeRuntime()
        with self.assertRaises(ExperimentError) as failure: await self.case.run_experiment(True,runtime,model)
        self.assertTrue(failure.exception.interrupted); self.assertEqual(model.ainvoke.await_count,2)
        self.assertEqual(runtime.writes,0); self.assertEqual(runtime.evaluations,0)

    async def test_repair_budget_stops_after_three_repairs(self):
        class Runtime(fixtures.FakeRuntime):
            async def evaluate(self):
                self.evaluations += 1
                return classify_evaluation({"status":0,"test_sets":[{"compile_success":True,"result":False}]})
        runtime = Runtime(); model = SimpleNamespace(ainvoke=AsyncMock(side_effect=[completion("revision-"+str(index)) for index in range(4)]))
        with self.assertRaises(ExperimentError) as failure: await self.case.run_experiment(True,runtime,model)
        self.assertEqual(failure.exception.category,"repair_budget"); self.assertEqual(model.ainvoke.await_count,4)
        self.assertEqual(runtime.evaluations,4)

    async def test_manual_edit_during_model_generation_is_not_overwritten(self):
        runtime = fixtures.FakeRuntime()
        async def answer(*args,**kwargs):
            runtime.files[runtime.task_id] = "human changes"
            return completion()
        model = SimpleNamespace(ainvoke=AsyncMock(side_effect=answer))
        with self.assertRaises(ExperimentError) as failure: await self.case.run_experiment(True,runtime,model)
        self.assertEqual(failure.exception.category,"concurrent_edit"); self.assertEqual(runtime.files["one"],"human changes")
        self.assertEqual(runtime.writes,0); self.assertEqual(runtime.evaluations,0)


class EvaluationRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_header_refresh_never_reloads_the_active_experiment_page(self):
        client=EduCoderClient({})
        client.page=SimpleNamespace(reload=AsyncMock())
        async def refresh(*args,**kwargs): client.header_ready.set()
        refresh_page=SimpleNamespace(goto=AsyncMock(side_effect=refresh),close=AsyncMock())
        response=SimpleNamespace(status=200,ok=True,json=AsyncMock(return_value={'status':0}))
        client.context=SimpleNamespace(new_page=AsyncMock(return_value=refresh_page),request=SimpleNamespace(fetch=AsyncMock(return_value=response)))
        await client.request('/api/tasks/task.json')
        client.page.reload.assert_not_awaited(); refresh_page.close.assert_awaited_once()

    async def test_build_ticket_does_not_poll_before_platform_compute_start(self):
        runtime,button=self.runtime()
        listeners={}
        runtime.page.on=lambda event,callback:listeners.update({event:callback})
        runtime.client.request=AsyncMock()
        async def click(**kwargs):
            listeners['response'](SimpleNamespace(url='https://data.educoder.net/api/tasks/task/game_build.json',request=SimpleNamespace(method='POST'),json=AsyncMock(return_value={'resubmit':2,'sec_key':'private-ticket'})))
            await asyncio.sleep(0)
        button.click.side_effect=click
        runtime.task=AsyncMock(side_effect=[{'game':{'status':0,'evaluate_count':0}},{'game':{'status':2,'evaluate_count':1}}])
        self.assertEqual((await runtime.evaluate()).status,'passed')
        runtime.client.request.assert_not_awaited()

    async def test_pending_task_snapshot_does_not_abort_or_resubmit_evaluation(self):
        runtime,button=self.runtime()
        runtime.task=AsyncMock(side_effect=[{"game":{"status":0,"evaluate_count":0}},None,{"game":{"status":2,"evaluate_count":1}}])
        report=await runtime.evaluate()
        self.assertEqual(report.status,"passed"); self.assertEqual(button.click.await_count,1)

    async def test_partial_task_snapshot_is_only_tolerated_during_evaluation(self):
        runtime=ExperimentRuntime(SimpleNamespace(request=AsyncMock(return_value={"status":0,"message":"busy"})),None,"123","COURSE")
        runtime.task_id="task"
        self.assertIsNone(await runtime.task(allow_pending=True))
        with self.assertRaises(Exception): await runtime.task()

    def test_platform_resource_failure_does_not_trigger_code_repair(self):
        report=classify_evaluation({"status":0,"last_compile_output":"服务中断导致测评失败，请稍后重试"})
        self.assertEqual(report.kind,"environment"); self.assertFalse(report.repairable)

    async def test_full_file_editor_input_uses_paste_instead_of_typing_auto_indented_lines(self):
        textarea=SimpleNamespace(evaluate=AsyncMock(return_value=True))
        editor=SimpleNamespace(wait_for=AsyncMock(),click=AsyncMock(),evaluate=AsyncMock(return_value=True),locator=lambda *args:textarea)
        page=SimpleNamespace(locator=lambda *args:SimpleNamespace(first=editor),keyboard=SimpleNamespace(press=AsyncMock(),insert_text=AsyncMock()),wait_for_timeout=AsyncMock())
        runtime=ExperimentRuntime(None,page,"123","COURSE")
        text="class Main {\n    // Begin\n    // End\n}\n"
        await runtime.sync_editor(text)
        self.assertIn("ClipboardEvent",textarea.evaluate.await_args.args[0]); self.assertEqual(textarea.evaluate.await_args.args[1],text)
        page.keyboard.insert_text.assert_not_awaited()

    async def test_web_editor_save_uses_editor_tab_type_not_challenge_question_type(self):
        client=SimpleNamespace(request=AsyncMock(return_value={"status":0}))
        runtime=ExperimentRuntime(client,None,"123","COURSE"); runtime.task_id="task"; runtime.instance_id="instance"
        runtime.read=AsyncMock(side_effect=["old","new"])
        await runtime.write("Main.java","old","new",{"st":0,"game":{"id":1},"challenge":{"path":"Main.java"}})
        self.assertEqual(client.request.await_args.kwargs["data"]["tab_type"],1)

    async def test_pending_header_observers_are_cancelled_instead_of_masking_the_execution_error(self):
        client=EduCoderClient({}); client.context=SimpleNamespace(remove_listener=lambda *args:None)
        task=asyncio.create_task(asyncio.Event().wait()); client.observers.add(task)
        await asyncio.wait_for(client.__aexit__(None,None,None),1)
        self.assertTrue(task.cancelled()); self.assertFalse(client.observers)

    def runtime(self):
        button = SimpleNamespace(wait_for=AsyncMock(),click=AsyncMock())
        page = SimpleNamespace(on=lambda *args:None, remove_listener=lambda *args:None, get_by_role=lambda *args,**kwargs:button)
        runtime = ExperimentRuntime(SimpleNamespace(),page,"123","COURSE")
        runtime.task_id="task"; runtime.instance_id="instance"; runtime.refresh=AsyncMock()
        runtime.task=AsyncMock(return_value={"game":{"status":0,"evaluate_count":0}})
        return runtime,button

    async def test_unknown_result_never_clicks_evaluate_a_second_time(self):
        runtime,button=self.runtime(); runtime.evaluation_timeout=0
        for _ in range(2):
            with self.assertRaises(ExperimentError) as failure: await runtime.evaluate()
            self.assertTrue(failure.exception.unknown)
        self.assertEqual(button.click.await_count,1); runtime.refresh.assert_not_awaited()

    async def test_known_failed_feedback_is_returned_only_after_a_fresh_evaluation_count(self):
        runtime,button=self.runtime()
        async def click(**kwargs): runtime.evaluation_payload={"status":0,"last_compile_output":"javac error: cannot find symbol"}
        button.click.side_effect=click
        runtime.task=AsyncMock(side_effect=[{"game":{"status":0,"evaluate_count":0}},{"game":{"status":0,"evaluate_count":1}}])
        report=await runtime.evaluate()
        self.assertEqual(report.kind,"compile"); self.assertFalse(runtime.evaluation_sent)

    async def test_passed_receipt_is_read_without_replaying_a_pending_submission(self):
        runtime,button=self.runtime(); runtime.evaluation_sent=True
        runtime.task=AsyncMock(return_value={"game":{"status":2,"id":1,"evaluate_count":1}})
        report=await runtime.evaluate()
        self.assertEqual(report.status,"passed"); button.click.assert_not_awaited()

    async def test_task_readback_reports_a_confirmed_failure_even_if_browser_response_event_is_missing(self):
        runtime,button=self.runtime()
        runtime.task=AsyncMock(side_effect=[{"game":{"status":0,"evaluate_count":0}},
            {"game":{"status":0,"evaluate_count":1},"in_review":False,"sets_error_count":1,
             "test_sets":[{"result":False,"compile_success":1}],"last_compile_output":"One test did not match"}])
        report=await runtime.evaluate()
        self.assertEqual(report.status,"failed"); self.assertEqual(report.kind,"test")
        self.assertEqual(report.evidence["source"],"task_state_readback"); self.assertFalse(runtime.evaluation_sent)
