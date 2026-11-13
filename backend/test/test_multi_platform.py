from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import tempfile
import threading
import unittest
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from fastapi.testclient import TestClient
from playwright.async_api import async_playwright

from app.config import Settings
from app.db import Database
from app.events import EventHub
from app.services.browser import chrome_executable
from app.services.learning.browser import LearningBrowser, RUNTIME, create_learning_browser
from app.services.learning.multi_platform import OCSLearningBrowser, PLATFORM_RUNTIME, UPSTREAM
from app.services.learning.service import LearningService
from app.services.learning.store import LearningStore
from app.services.platform_auth import restore_state
from app.services.platforms import platform_registry
from app.services.platforms.catalog import CATALOG, platform_url

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = {
    "zhihuishu": '<div class="clearfix video current_play" data-id="one"><span class="catalogue_title">Done</span><i class="time_icofinish"></i></div><div class="clearfix video" data-id="two"><span class="catalogue_title">Pending</span></div>',
    "icourse": '<div class="learn-unit-tabs-scroll"><button data-id="one" class="rgba"><i class="lucide-circle-check"></i>Done</button><button data-id="two">Pending</button></div>',
    "icve": '<button class="s_point" itemtype="video" itemid="one" title="Done"><i class="done_icon_show"></i></button><button class="s_point" itemtype="video" itemid="two" title="Pending"></button>',
    "zjy": '<div class="coursePreviewIndex"><div class="items">Done</div><div class="items">Pending</div></div>',
    "yuketang": '<div class="leaf-item is-active" data-id="one"><span class="leaf-item-title">Done</span><span class="leaf-item-tag">视频</span><i class="icon-yuanquangou"></i></div><div class="leaf-item" data-id="two"><span class="leaf-item-title">Pending</span><span class="leaf-item-tag">视频</span></div>',
}


def database_fixture(folder):
    settings = Settings(data_root=Path(folder))
    settings.ensure_directories()
    database = Database(settings)
    database.initialize()
    LearningStore(database).initialize()
    return settings, database


class PlatformContractTests(unittest.TestCase):
    def test_catalog_covers_every_upstream_project_and_real_local_logo(self):
        self.assertEqual(set(CATALOG), {"chaoxing","zhihuishu","icourse","icve","zjy","yuketang","educoder"})
        self.assertEqual({item["name"] for item in platform_registry.list()},set(CATALOG))
        manifest=json.loads((ROOT/"third_party/ocs/upstream-manifest.json").read_text())
        for name,spec in CATALOG.items():
            if name == "educoder":
                self.assertTrue((ROOT/"desktop/public/platforms"/spec["logo"]).is_file())
                self.assertFalse(spec["learning"])
                self.assertTrue(spec["assignments"])
                continue
            source=ROOT/"third_party/ocs/upstream/projects"/spec["source"]
            digest=hashlib.sha256(source.read_bytes()).hexdigest()
            self.assertEqual(digest,manifest["source_hashes"]["projects/"+spec["source"]])
            self.assertTrue((ROOT/"desktop/public/platforms"/spec["logo"]).is_file())
            self.assertEqual(platform_url(name,spec["home_url"]),spec["home_url"])
        self.assertIn("MIT License",(ROOT/"third_party/ocs/upstream/LICENSE").read_text())
        runtime=ROOT/"backend/app/services/learning/ocs-study.js"
        self.assertEqual(hashlib.sha256(runtime.read_bytes()).hexdigest(),manifest["runtime_sha256"])

    def test_platform_url_boundaries_reject_other_sites_credentials_and_local_targets(self):
        for url in ["https://icve.com.cn.evil.test/course","https://evilicve.com.cn/","http://www.icve.com.cn/","https://name:secret@www.icve.com.cn/","https://127.0.0.1/course","file:///course","https://www.icve.com.cn:8877/course"]:
            with self.subTest(url=url), self.assertRaises(ValueError): platform_url("icve",url)
        self.assertEqual(platform_url("zhihuishu","https://study.polymas.com/hike"),"https://study.polymas.com/hike")

    def test_course_identity_is_platform_scoped_and_adding_one_does_not_hide_others(self):
        with tempfile.TemporaryDirectory() as folder:
            settings,database=database_fixture(folder)
            store=LearningStore(database)
            records=[]
            for platform in ("icve","yuketang"):
                account=database.create_account(platform,platform,settings)
                item={"courseId":"same","classId":"same","courseName":"First","url":CATALOG[platform]["home_url"]}
                initial=store.upsert_courses(account["id"],[item],0)[0]
                records.append(initial)
                store.upsert_courses(account["id"],[{**item,"courseId":"second","courseName":"Second"}],0,replace=False)
                self.assertEqual(len(store.courses(account["id"],0)),2)
                self.assertEqual(initial["platform"],platform)
                self.assertIsInstance(create_learning_browser(account),OCSLearningBrowser)
            self.assertNotEqual(records[0]["id"],records[1]["id"])

    def test_api_platforms_and_course_import_use_local_services(self):
        from app import main
        with tempfile.TemporaryDirectory() as folder:
            settings,database=database_fixture(folder)
            old_database,old_settings=main.database,main.settings
            main.database,main.settings=database,settings
            try:
                with TestClient(main.app) as client:
                    self.assertEqual(len(client.get("/api/platforms").json()),7)
                    self.assertEqual(set(client.get("/api/learning/capabilities").json()["platforms"]),{name for name,spec in CATALOG.items() if spec["learning"]})
                    response=client.post("/api/accounts",json={"name":"MOOC test","platform":"icourse","entry_url":"https://www.icourse163.org/home.htm"})
                    self.assertEqual(response.status_code,200)
                    account=response.json()
                    for number in (1,2):
                        response=client.post("/api/learning/courses",json={"account_id":account["id"],"name":"Course "+str(number),"url":"https://www.icourse163.org/learn/test-"+str(number)})
                        self.assertEqual(response.status_code,200,response.text)
                        self.assertEqual(response.json()["platform"],"icourse")
                    self.assertEqual(len(client.get("/api/learning/courses",params={"account_id":account["id"]}).json()),2)
                    self.assertEqual(client.post("/api/learning/courses",json={"account_id":account["id"],"name":"Wrong","url":"https://evil.test/learn/1"}).status_code,422)
            finally: main.database,main.settings=old_database,old_settings


class PlatformRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_verification_uses_refreshed_site_tokens_and_tracks_resource_popups(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_GET(self):
                html = '<audio></audio>' if self.path=='/lesson' else '<a class="s_point" itemtype="audio" itemid="one" title="Audio" target="_blank" href="/lesson">Audio</a><script>document.documentElement.dataset.auth=localStorage.getItem("token")||"";</script>'
                self.send_response(200);self.send_header("Content-Type","text/html; charset=utf-8");self.end_headers();self.wfile.write(html.encode())
        server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        browser=None
        try:
            with tempfile.TemporaryDirectory() as folder:
                settings,database=database_fixture(folder)
                account=database.create_account("Token fixture","icve",settings)
                browser=OCSLearningBrowser(account,headless=True,authenticate=False)
                try:
                    await browser.open()
                    url=f"http://127.0.0.1:{server.server_port}/course"
                    await browser.page.goto(url)
                    await browser.page.evaluate("localStorage.setItem('token','first')")
                    await browser.navigate(url)
                    await browser.verify_catalog()
                    self.assertEqual(await browser.verify_page.get_attribute("html","data-auth"),"first")
                    await browser.page.evaluate("localStorage.setItem('token','second')")
                    await browser.verify_catalog()
                    self.assertEqual(await browser.verify_page.get_attribute("html","data-auth"),"second")
                    catalog_page=browser.page
                    await browser.open_chapter("one")
                    self.assertTrue(browser.page.url.endswith("/lesson"))
                    self.assertIs(browser.catalog_page,catalog_page)
                    self.assertTrue(await browser.catalog())
                    attachments,frames=await browser.inspect()
                    self.assertTrue(any(item["data"]["media"] and item["job_id"] in attachments for item in frames))
                finally:
                    await browser.close();browser=None
        finally:
            if browser: await browser.close()
            server.shutdown();server.server_close();thread.join(2)

    async def test_all_new_platforms_read_real_markers_and_keep_unknown_as_incomplete(self):
        async with async_playwright() as playwright:
            browser=await playwright.chromium.launch(headless=True,executable_path=str(chrome_executable()))
            try:
                for platform,html in FIXTURES.items():
                    with self.subTest(platform=platform):
                        page=await browser.new_page()
                        await page.set_content(html)
                        if platform=="zjy":
                            await page.evaluate("document.querySelector('.coursePreviewIndex').__vue__={list:[{id:'one',name:'Done',progress:100,fileType:'视频'},{id:'two',name:'Pending',fileType:'视频'}]}")
                        for source in (RUNTIME,UPSTREAM,PLATFORM_RUNTIME): await page.evaluate(source)
                        result=await page.evaluate("p=>window.__studioCourse.catalog(p)",platform)
                        self.assertEqual([item["completed"] for item in result],[True,False],result)
                        self.assertTrue(all(item["navigable"] for item in result))
                        if platform=="zhihuishu":
                            self.assertEqual(await page.evaluate("()=>new window.__studioOCSUpstream.zhs.StudyVideoH5().getNext({next:false,restudy:false}).dataset.id"),"two")
                        await page.close()
            finally: await browser.close()

    async def test_zhihuishu_variants_use_upstream_finish_markers(self):
        variants=[
            '<div class="resource-box"></div><div class="resources-item"><span class="file-name">One</span><i class="isFinish"></i></div>',
            '<div><span class="child-main">One</span><i class="child-time"></i><i class="finish-icon"></i></div>',
            '<div class="chapter-content"><div class="chapter-item"><div class="chapter-content-second">One<i class="finish-icon"></i></div></div></div>',
            '<div><div><i class="source-icon"></i>One<i class="select"></i></div></div>',
            '<div class="el-tree-node"><span class="file-name">One</span><label class="success"></label><div class="el-tree-node__children"></div></div>',
        ]
        async with async_playwright() as playwright:
            browser=await playwright.chromium.launch(headless=True,executable_path=str(chrome_executable()))
            try:
                for html in variants:
                    page=await browser.new_page()
                    await page.set_content(html)
                    for source in (RUNTIME,UPSTREAM,PLATFORM_RUNTIME): await page.evaluate(source)
                    chapters=await page.evaluate("window.__studioCourse.catalog('zhihuishu')")
                    self.assertTrue(chapters and all(item["completed"] for item in chapters),chapters)
                    await page.close()
            finally: await browser.close()

    async def test_local_storage_and_cookie_restore_and_media_pause_remain_local(self):
        async with async_playwright() as playwright:
            browser=await playwright.chromium.launch(headless=True,executable_path=str(chrome_executable()))
            try:
                context=await browser.new_context()
                with tempfile.TemporaryDirectory() as folder:
                    path=Path(folder)/"storage.json"
                    path.write_text(json.dumps({"cookies":[{"name":"test","value":"session","domain":"fixture.icve.com.cn","path":"/"}],"origins":[{"origin":"https://fixture.icve.com.cn","localStorage":[{"name":"token","value":"fixture-jwt"}]}]}))
                    await restore_state(context,str(path))
                page=await context.new_page()
                await page.route("https://fixture.icve.com.cn/**",lambda route:route.fulfill(body="<audio></audio>",content_type="text/html"))
                await page.goto("https://fixture.icve.com.cn/course")
                self.assertEqual(await page.evaluate("localStorage.getItem('token')"),"fixture-jwt")
                self.assertTrue(any(item["name"]=="test" for item in await context.cookies()))
                for source in (RUNTIME,UPSTREAM,PLATFORM_RUNTIME): await page.evaluate(source)
                self.assertTrue((await page.evaluate("window.__studioLearning.pause()"))["paused"])
            finally: await browser.close()

    async def test_local_icve_workflow_plays_then_requires_fresh_platform_completion(self):
        buffer=io.BytesIO()
        with wave.open(buffer,"wb") as out:
            out.setnchannels(1);out.setsampwidth(2);out.setframerate(8000);out.writeframes(b"\0\0"*8000)
        audio=base64.b64encode(buffer.getvalue()).decode()
        state={"finished":False,"verified":0}
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_POST(self):
                state["finished"]=True
                self.send_response(200);self.end_headers()
            def do_GET(self):
                state["verified"]+=1
                done='<i class="done_icon_show"></i>' if state["finished"] else ''
                html='<button class="s_point" itemtype="audio" itemid="audio1" title="Audio">'+done+'</button><audio src="data:audio/wav;base64,'+audio+'" onended="fetch(\'/finished\',{method:\'POST\'})"></audio>'
                self.send_response(200);self.send_header("Content-Type","text/html; charset=utf-8");self.end_headers();self.wfile.write(html.encode())
        server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        class FixtureBrowser(OCSLearningBrowser):
            def __init__(self,account,**kwargs): super().__init__(account,headless=True,authenticate=False)
        try:
            with tempfile.TemporaryDirectory() as folder:
                settings,database=database_fixture(folder)
                account=database.create_account("ICVE test","icve",settings)
                database.update_account(account["id"],status="ready")
                store=LearningStore(database)
                course=store.upsert_courses(account["id"],[{"courseId":"fixture","classId":"","courseName":"Audio","url":f"http://127.0.0.1:{server.server_port}/course"}],0)[0]
                service=LearningService(database,settings,EventHub(),browser_factory=FixtureBrowser)
                run=service.start(course["id"])
                await asyncio.wait_for(service.tasks[run["id"]],35)
                result=store.run(run["id"])
                self.assertEqual(result["status"],"completed",result.get("error"))
                self.assertTrue(state["finished"])
                self.assertGreaterEqual(state["verified"],3)
                self.assertFalse(service.browsers)
        finally: server.shutdown();server.server_close();thread.join(2)
