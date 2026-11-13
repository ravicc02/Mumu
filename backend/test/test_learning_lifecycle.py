import asyncio
import base64
import io
import tempfile
import unittest
import wave
from pathlib import Path

from app.services.learning.browser import LearningBrowser


class NativeLearningLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_adopting_manual_play_does_not_replay_pause_or_change_player_options(self):
        output = io.BytesIO()
        with wave.open(output, "wb") as audio:
            audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(8000)
            audio.writeframes(b"\0\0" * 8000 * 10)
        source = base64.b64encode(output.getvalue()).decode()
        with tempfile.TemporaryDirectory() as root:
            browser = LearningBrowser({"profile_dir": str(Path(root) / "account")}, authenticate=False)
            try:
                await browser.open()
                await browser.page.set_content('<audio controls src="data:audio/wav;base64,' + source + '"></audio><button>Play</button>')
                await browser.page.evaluate("""() => {
                    const media = document.querySelector('audio');
                    const nativePlay = media.play.bind(media);
                    window.playCalls = window.pauseEvents = 0;
                    media.play = () => { window.playCalls++; return nativePlay(); };
                    media.addEventListener('pause', () => window.pauseEvents++);
                    document.querySelector('button').onclick = () => {
                        media.volume = .45; media.playbackRate = 1.25; media.play();
                    };
                }""")
                await browser.page.get_by_role("button", name="Play").click()
                await browser.page.wait_for_function("document.querySelector('audio').currentTime > .1")
                before = await browser.page.evaluate("document.querySelector('audio').currentTime")
                browser.learning_options = {"volume": 0, "playback_rate": 1}
                result = await browser.play(browser.page.main_frame)
                self.assertTrue(result["started"])
                self.assertTrue(result["adopted"])
                await browser.page.wait_for_function("position => document.querySelector('audio').currentTime > position + .2", arg=before)
                state = await browser.page.evaluate("""() => { const media = document.querySelector('audio'); return {
                    playCalls, pauseEvents, paused: media.paused, volume: media.volume, rate: media.playbackRate
                }; }""")
                self.assertEqual(state, {"playCalls": 1, "pauseEvents": 0, "paused": False, "volume": .45, "rate": 1.25})
            finally:
                await browser.close()

    async def test_closing_and_reopening_viewers_keeps_the_same_headless_worker_running(self):
        with tempfile.TemporaryDirectory() as root:
            browser = LearningBrowser({"profile_dir": str(Path(root) / "account")}, authenticate=False)
            try:
                await browser.open()
                await browser.page.set_content(
                    '<canvas id="screen" width="400" height="120"></canvas><script>'
                    'window.workerTicks=0;setInterval(()=>{workerTicks++;const c=document.getElementById("screen").getContext("2d");'
                    'c.clearRect(0,0,400,120);c.fillText(String(workerTicks),20,20);},50)</script>'
                )
                worker_context, worker_page = browser.context, browser.page
                panel = await browser.live.subscribe()
                viewer = await browser.live.subscribe()
                self.assertEqual((await asyncio.wait_for(viewer.get(), 6))["type"], "frame")
                shared_client = browser.live.client
                await browser.live.unsubscribe(viewer)
                self.assertIs(browser.live.client, shared_client)
                self.assertEqual(len(browser.live.subscribers), 1)
                self.assertEqual((await asyncio.wait_for(panel.get(), 6))["type"], "frame")
                await browser.live.unsubscribe(panel)
                ticks = await worker_page.evaluate("window.workerTicks")
                await worker_page.wait_for_function("previous=>window.workerTicks>previous+2", arg=ticks)
                self.assertIsNone(browser.live.client)
                self.assertFalse(browser.connection_lost.is_set())
                reopened = await browser.live.subscribe()
                self.assertEqual((await asyncio.wait_for(reopened.get(), 6))["type"], "frame")
                self.assertIs(browser.context, worker_context)
                self.assertIs(browser.page, worker_page)
                self.assertTrue(worker_context.browser.is_connected())
                await browser.live.unsubscribe(reopened)
                await worker_context.close()
                await asyncio.wait_for(browser.connection_lost.wait(), 3)
                self.assertIn(browser.failure_kind, {"context_closed", "browser_disconnected", "active_page_closed"})
            finally: await browser.close()

    async def test_headless_chapter_popup_becomes_live_page_and_closing_it_reports_loss(self):
        with tempfile.TemporaryDirectory() as root:
            browser = LearningBrowser({"profile_dir": str(Path(root) / "account")}, authenticate=False)
            try:
                await browser.open()
                async def route(request):
                    if "studentstudy" in request.request.url:
                        html = '<title>Chapter media</title><video></video>'
                    else:
                        html = '<div><a onclick="getTeacherAjax(\'c\',\'class\',\'chapter\')">Chapter</a><input class="jobUnfinishCount" value="1"></div><script>window.getTeacherAjax=()=>window.open("/studentstudy", "_blank")</script>'
                    await request.fulfill(content_type="text/html", body=html)
                await browser.context.route("**/*", route)
                await browser.navigate("http://127.0.0.1:19346/course")
                catalog = browser.page
                await browser.open_chapter("chapter")
                self.assertIn("studentstudy", browser.page.url)
                self.assertIsNot(browser.page, catalog)
                current, _ = await browser.resolve_live_page()
                self.assertIs(current, browser.page)
                self.assertFalse(browser.connection_lost.is_set())
                await browser.page.close()
                await asyncio.wait_for(browser.connection_lost.wait(), 3)
                self.assertEqual(browser.failure_kind, "active_page_closed")
                self.assertFalse(catalog.is_closed())
            finally: await browser.close()

    async def test_closing_a_real_headed_or_headless_context_reports_browser_loss(self):
        for headless in (True, False):
            with self.subTest(headless=headless), tempfile.TemporaryDirectory() as root:
                browser = LearningBrowser({"profile_dir": str(Path(root) / "account")}, headless=headless, authenticate=False)
                try:
                    await browser.open()
                    await browser.page.set_content("<h1>Task lifecycle test</h1>")
                    self.assertFalse(browser.connection_lost.is_set())
                    await browser.context.close()
                    await asyncio.wait_for(browser.connection_lost.wait(), 3)
                    self.assertIn("后台浏览器" if headless else "窗口已关闭", browser.close_reason)
                    with self.assertRaises(RuntimeError): browser.ensure_connected()
                finally: await browser.close()

    async def test_expected_shutdown_and_viewer_detach_do_not_report_browser_loss(self):
        with tempfile.TemporaryDirectory() as root:
            browser = LearningBrowser({"profile_dir": str(Path(root) / "account")}, authenticate=False)
            try:
                await browser.open()
                self.assertTrue(browser.headless)
                await browser.page.set_content('<input value="interactive"><div style="height:2500px"></div>')
                queue = await browser.live.subscribe()
                frame = await asyncio.wait_for(queue.get(), 6)
                self.assertEqual(frame["type"], "frame")
                await browser.live.unsubscribe(queue)
                self.assertFalse(browser.connection_lost.is_set())
                self.assertFalse(browser.page.is_closed())
                await browser.close()
                self.assertFalse(browser.connection_lost.is_set())
            finally: await browser.close()
