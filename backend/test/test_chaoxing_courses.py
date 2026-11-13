import asyncio
import unittest

from playwright.async_api import async_playwright

from app.services.browser import chrome_executable
from app.services.chaoxing import _fetch_student_course_list, _parse_courses


COURSE = '''<div class="course"><a href="/mycourse/stucoursemiddle?courseid=101&amp;clazzid=201&amp;cpi=1">
<span class="course-name" title="Fixture course">Fixture course</span></a><p>开课时间：2026-11-06～2026-12-31</p></div>'''
URL = 'https://courses.fixture.test/visit/interaction'


class StudentCourseViewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.driver = await async_playwright().start()
        self.browser = await self.driver.chromium.launch(headless=True, executable_path=str(chrome_executable()))
        self.page = await self.browser.new_page()
        self.requests = []

    async def asyncTearDown(self):
        await self.browser.close()
        await self.driver.stop()

    async def fixture(self, *, current=False, status=200, legacy=False):
        tabs = '' if legacy else '<div class="course-tab"><div class="tab-item '+('' if current else 'current')+'" coursetype="0">我教的课</div><div class="tab-item '+('current' if current else '')+'" coursetype="1">我学的课</div></div>'
        content = COURSE if current or legacy else '<p>暂无课程</p>'
        html = tabs + '<div id="courseList">'+content+'''</div><script>
        document.querySelector('[coursetype="1"]')?.addEventListener('click', async () => {
          document.querySelector('[coursetype="0"]').classList.remove('current');
          document.querySelector('[coursetype="1"]').classList.add('current');
          const response = await fetch('/mooc-ans/visit/courselistdata', {method:'POST'});
          if (response.ok) document.querySelector('#courseList').innerHTML = await response.text();
        });</script>'''
        await self.page.route(URL, lambda route: route.fulfill(body=html, content_type='text/html'))
        async def data(route):
            self.requests.append(route.request.method)
            await asyncio.sleep(.2)
            await route.fulfill(status=status, body=COURSE if status==200 else 'service unavailable', content_type='text/html')
        await self.page.route('**/mooc-ans/visit/courselistdata', data)

    async def test_empty_teaching_view_switches_to_student_and_waits_for_courses(self):
        await self.fixture()
        url, html = await _fetch_student_course_list(self.page, URL)
        courses = _parse_courses(html, url)
        self.assertEqual(self.requests, ['POST'])
        self.assertEqual(len(courses), 1)
        self.assertEqual(courses[0]['startDate'], '2026-11-06')
        self.assertEqual(courses[0]['courseId'], '101')

    async def test_already_selected_student_view_does_not_reload_or_toggle(self):
        await self.fixture(current=True)
        url, html = await _fetch_student_course_list(self.page, URL)
        self.assertEqual(len(_parse_courses(html, url)), 1)
        self.assertEqual(self.requests, [])

    async def test_legacy_student_course_page_without_role_tabs_keeps_working(self):
        await self.fixture(legacy=True)
        url, html = await _fetch_student_course_list(self.page, URL)
        self.assertEqual(len(_parse_courses(html, url)), 1)
        self.assertEqual(self.requests, [])

    async def test_failed_student_list_request_is_not_reported_as_a_year_filter_miss(self):
        await self.fixture(status=503)
        with self.assertRaisesRegex(RuntimeError, 'HTTP 503'):
            await _fetch_student_course_list(self.page, URL)
