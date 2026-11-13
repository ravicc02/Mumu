from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.config import Settings
from app.db import Database
from app.services.tasks.runner import JobControl
from app.services.educoder import store
from app.services.educoder.client import EduCoderClient
from app.services.educoder.contracts import ContractError, course_record, experiment_record, paged, site_url
from app.services.educoder.executor import CodeFile, CodePatch, ExperimentError, challenge_list, execute_experiment, file_content, repository_path
from app.services.accounts.identity import cache_avatar, save_identity
from app.services.tasks.state import available_job_actions
from app.diagnostics.privacy import Scrubber


COURSE = {"id":"101","route_id":"CLASS_A","name":"Code classroom","url":"https://www.educoder.net/classrooms/CLASS_A/announcement","active":True,"accessible":True}


def experiment(passed=0):
    return {"id":123,"name":"JUnit exercise","checked_challenge_count":2,"finished_challenge_count":passed,"status":["提交中"],"end_time_s":"2099-11-13 23:59"}


class FakeRuntime:
    def __init__(self):
        self.work_id = "123"
        self.instance_id = "student-instance"
        self.task_id = "one"
        self.passed = set()
        self.files = {"one":"original one","two":"original two"}
        self.evaluations = self.writes = 0
        self.open = AsyncMock()
        self.refresh = AsyncMock()
        self.receipt = True
        self.client = SimpleNamespace(all_pages=self.all_pages)

    async def all_pages(self,*args,**kwargs):
        return [experiment(len(self.passed) if self.receipt else 0)]

    async def challenges(self):
        return [{"identifier":key,"name":key,"status":2 if key in self.passed else 0} for key in self.files]

    async def select(self,identifier):
        self.task_id = identifier
        return {"game":{"id":identifier,"status":0},"challenge":{"description":"Implement a test within the existing source file.","path":"Main.java"}}

    async def read(self,path): return self.files[self.task_id]

    async def write(self,path,old,content,data):
        if old != self.files[self.task_id]: raise ExperimentError("changed concurrently")
        self.files[self.task_id] = content
        self.writes += 1

    async def evaluate(self):
        self.evaluation_sent = False
        self.evaluations += 1
        self.passed.add(self.task_id)


class ContractTests(unittest.TestCase):
    def test_educoder_auth_headers_are_redacted_from_structures_and_request_errors(self):
        scrubber=Scrubber()
        clean=scrubber.clean({"Pc-Authorization":"private-session","X-EDU-Signature":"private-signature","sec_key":"judge-secret"})
        self.assertTrue(all(value=="[REDACTED]" for value in clean.values()))
        text=scrubber.text("Call log:\n  - Pc-Authorization: private-session\n  - X-EDU-Signature: private-signature")
        self.assertNotIn("private-session",text)
        self.assertNotIn("private-signature",text)
    def test_course_keeps_canonical_identity_and_route_alias(self):
        item = course_record({"id":101,"identifier":"CLASS_A","name":"Code classroom","is_end":False,"is_accessible":True,"first_category_url":"/classrooms/CLASS_A/announcement"})
        self.assertEqual((item["id"],item["route_id"]),("101","CLASS_A"))
        self.assertTrue(item["active"])

    def test_completion_and_submission_window_are_independent(self):
        raw = experiment()
        self.assertTrue(experiment_record(raw,COURSE)["submittable"])
        raw["status"] = ["已截止"]
        item = experiment_record(raw,COURSE)
        self.assertEqual(item["status"],"unfinished")
        self.assertFalse(item["submittable"])
        raw["status"] = ["已截止","补交中"]
        self.assertTrue(experiment_record(raw,COURSE)["submittable"])
        raw["finished_challenge_count"] = 2
        self.assertFalse(experiment_record(raw,COURSE)["submittable"])
        self.assertEqual(experiment_record(raw,COURSE)["status"],"finished")

    def test_unknown_or_missing_data_cannot_claim_ready_or_empty(self):
        raw = experiment(); raw["status"] = [99]
        self.assertFalse(experiment_record(raw,COURSE)["submittable"])
        raw.pop("finished_challenge_count")
        with self.assertRaises(ContractError): experiment_record(raw,COURSE)
        for payload in ({},{"courses":[]},{"courses":[],"count":True}):
            with self.assertRaises(ContractError): paged(payload,"courses")

    def test_homework_query_total_excludes_unpublished_and_nonmatching_records(self):
        rows = [{"homework_id":number} for number in range(4)]
        parsed,total = paged({"homeworks":rows,"all_count":6,"published_count":4,"query_total_count":4},"homeworks")
        self.assertEqual(total,4)
        self.assertEqual(parsed,rows)
        self.assertEqual(paged({"homeworks":[],"all_count":6,"query_total_count":0},"homeworks"),([],0))
        for invalid in (None,True,-1,"4"):
            with self.assertRaises(ContractError): paged({"homeworks":rows,"all_count":6,"query_total_count":invalid},"homeworks")

    def test_resource_boundaries_and_full_file_decoding(self):
        for url in ("https://www.educoder.net.evil.test/","http://www.educoder.net/","https://name:pass@www.educoder.net/","https://www.educoder.net:8877/"):
            with self.assertRaises(ContractError): site_url(url)
        for path in ("../Main.java","/root/Main.java","a\\Main.java",""):
            with self.assertRaises(ContractError): repository_path(path)
        self.assertEqual(file_content({"file_type":"txt","content":{"content":base64.b64encode("完整代码".encode()).decode()}}),"完整代码")
        with self.assertRaises(ContractError): file_content({"content":"partial visible lines"})
        with self.assertRaises(ContractError): challenge_list([{"identifier":"one"},{"identifier":"one"}])


class EducoderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.settings = Settings(data_root=Path(self.root.name))
        self.settings.ensure_directories()
        self.db = Database(self.settings); self.db.initialize()
        self.account = self.db.create_account("pending","educoder",self.settings)
        self.db.update_account(self.account["id"],status="ready",platform_user_id="student-one")
        store.save_courses(self.db,self.account["id"],[COURSE])
        store.select_courses(self.db,self.account["id"],[COURSE["id"]])
        value = experiment_record(experiment(),COURSE)
        self.assignment = self.db.upsert_assignment(self.account["id"],"educoder",value,self.settings)
        self.db.update_assignment_assessment(self.assignment,value["assessment"])

    async def asyncTearDown(self): self.root.cleanup()

    async def run_experiment(self,auto_submit,runtime=None,llm=None):
        job = self.db.create_job(self.account["id"],self.assignment,auto_submit)
        self.db.transition_job(job["id"],"running")
        model = llm or SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(completion=CodePatch(summary="Implemented the requested test",files=[CodeFile(path="Main.java",content="updated complete source")]))))
        runtime = runtime or FakeRuntime()
        result = await execute_experiment(self.db,self.db.get_job(job["id"]),None,model,AsyncMock(),JobControl(),self.db.diagnostics.trace("assignment",job["id"]),runtime=runtime)
        return result,runtime,model

    async def test_manual_review_saves_one_stage_without_grading(self):
        result,runtime,model = await self.run_experiment(False)
        self.assertEqual(result["status"],"needs_intervention")
        self.assertEqual(result["passed"],0)
        self.assertEqual(runtime.writes,1)
        self.assertEqual(runtime.evaluations,0)
        runtime.refresh.assert_not_awaited()
        self.assertEqual(model.ainvoke.await_count,1)

    async def test_automatic_run_finishes_only_after_stage_and_classroom_receipts(self):
        result,runtime,_ = await self.run_experiment(True)
        self.assertEqual(result["status"],"completed")
        self.assertEqual(result["submission_status"],"submitted_verified")
        self.assertEqual(runtime.evaluations,2)
        self.assertEqual(runtime.writes,2)

    async def test_passed_work_skips_the_model_and_environment(self):
        runtime = FakeRuntime(); runtime.passed = {"one","two"}
        result,_,model = await self.run_experiment(True,runtime)
        self.assertEqual(result["status"],"completed")
        self.assertEqual(runtime.evaluations,0)
        runtime.open.assert_not_awaited(); model.ainvoke.assert_not_awaited()

    async def test_missing_classroom_receipt_cannot_become_success(self):
        runtime = FakeRuntime(); runtime.receipt = False
        with self.assertRaises(ExperimentError) as caught: await self.run_experiment(True,runtime)
        self.assertTrue(caught.exception.unknown)
        self.assertEqual(runtime.evaluations,2)

    async def test_model_cannot_write_an_unlisted_file(self):
        runtime = FakeRuntime()
        model = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(completion=CodePatch(summary="bad path",files=[CodeFile(path="secret.txt",content="wrong")]))))
        with self.assertRaises(ExperimentError): await self.run_experiment(False,runtime,model)
        self.assertEqual(runtime.writes,0)

    async def test_pagination_is_complete_and_rejects_repeated_pages(self):
        from app.services.educoder.adapter import discover_courses
        source = SimpleNamespace(identity=AsyncMock(return_value={"platform_user_id":"student-one","username":"student-one"}),all_pages=AsyncMock(return_value=[{"id":101,"identifier":"CLASS_A","name":"Code classroom","is_end":False,"is_accessible":True,"first_category_url":COURSE["url"]}]))
        with patch("app.services.educoder.adapter.EduCoderClient") as factory:
            factory.return_value.__aenter__ = AsyncMock(return_value=source)
            factory.return_value.__aexit__ = AsyncMock(return_value=None)
            await discover_courses(self.db,self.account["id"])
        self.assertEqual(source.all_pages.await_args.args[2]["category"],"study")
        client = EduCoderClient({})
        client.request = AsyncMock(side_effect=[{"courses":[{"id":"a"}],"count":2},{"courses":[{"id":"b"}],"count":2}])
        self.assertEqual(len(await client.all_pages("/api/test.json","courses")),2)
        client.request = AsyncMock(side_effect=[{"courses":[{"id":"a"}],"count":2},{"courses":[{"id":"a"}],"count":2}])
        with self.assertRaises(ContractError): await client.all_pages("/api/test.json","courses")

    async def test_homework_collection_stops_at_filtered_total_without_requesting_an_empty_page(self):
        client = EduCoderClient({})
        rows = [{"homework_id":number} for number in range(1,5)]
        client.request = AsyncMock(return_value={"homeworks":rows,"all_count":6,"query_total_count":4})
        self.assertEqual(await client.all_pages("/api/test.json","homeworks",{"type":4}),rows)
        client.request.assert_awaited_once()
        client.request = AsyncMock(return_value={"homeworks":[],"all_count":6,"query_total_count":0})
        self.assertEqual(await client.all_pages("/api/test.json","homeworks"),[])
        client.request.assert_awaited_once()

    async def test_filtered_pagination_still_rejects_missing_repeated_or_changing_pages(self):
        client = EduCoderClient({})
        first={"homeworks":[{"homework_id":1}],"all_count":8,"query_total_count":2}
        for second in (
            {"homeworks":[],"all_count":8,"query_total_count":2},
            {"homeworks":[{"homework_id":1}],"all_count":8,"query_total_count":2},
            {"homeworks":[{"homework_id":2}],"all_count":8,"query_total_count":3},
        ):
            client.request = AsyncMock(side_effect=[first,second])
            with self.assertRaises(ContractError): await client.all_pages("/api/test.json","homeworks")

    async def test_all_selected_courses_failing_is_failed_not_a_partial_empty_success(self):
        from app import main
        report={"assignments":[],"total_courses":1,"processed_courses":1,"failed_courses":1,
            "failed_course_ids":[COURSE["id"]],"errors":[{"course_id":COURSE["id"],"error":"fixture contract failure"}]}
        platform=SimpleNamespace(collect_assignments=AsyncMock(return_value=report))
        with patch.object(main,"database",self.db), patch.object(main,"effective_settings",return_value=self.settings), patch.object(main.platform_registry,"get",return_value=platform):
            with self.assertRaisesRegex(RuntimeError,"全部课程采集失败"):
                await main.perform_collection(self.account["id"],0,scheduled=False)
        run=self.db.list_collection_runs(self.account["id"])[0]
        self.assertEqual(run["status"],"failed")
        self.assertEqual(run["errors"][0]["error"],"fixture contract failure")
        self.assertTrue(self.db.get_assignment(self.assignment)["active"])

    async def test_course_selection_is_account_scoped_and_snapshot_does_not_hide_other_courses(self):
        other = {**COURSE,"id":"202","route_id":"CLASS_B","name":"Other"}
        store.save_courses(self.db,self.account["id"],[COURSE,other])
        with self.assertRaises(ContractError): store.select_courses(self.db,self.account["id"],["missing"])
        self.assertTrue(store.courses(self.db,self.account["id"])[0]["selected"])
        other_value = experiment_record(experiment(),other)
        other_id = self.db.upsert_assignment(self.account["id"],"educoder",other_value,self.settings)
        self.db.finalize_assignment_snapshot(self.account["id"],0,"new-run",course_ids=[COURSE["id"]])
        self.assertTrue(self.db.get_assignment(other_id)["active"])
        self.assertFalse(self.db.get_assignment(self.assignment)["active"])

    async def test_wrong_identity_is_not_saved_and_avatar_download_has_a_host_boundary(self):
        page = SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(get=AsyncMock())))
        with patch("app.services.accounts.identity.read_identity",AsyncMock(return_value={"name":"Other","platform_user_id":"wrong"})):
            with self.assertRaises(RuntimeError): await save_identity(self.db,self.account["id"],page)
        self.assertEqual(self.db.get_account(self.account["id"])["name"],"pending")
        self.assertIsNone(await cache_avatar(self.account,{"avatar_url":"https://127.0.0.1/private"},page.context))
        page.context.request.get.assert_not_awaited()

    async def test_reviewed_educoder_task_uses_viewer_and_can_continue_without_enabling_evaluation(self):
        actions = available_job_actions({"platform":"educoder","status":"needs_intervention","interactive":1,"submission_status":"filled_pending_review"},controlled=False,browser=True,retained=True)
        self.assertIn("view_browser",actions)
        self.assertIn("continue",actions)
        self.assertNotIn("open_browser",actions)
