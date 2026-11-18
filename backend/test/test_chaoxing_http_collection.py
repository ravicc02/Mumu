import unittest
from unittest.mock import AsyncMock, patch

from app.services.assignments import chaoxing

COURSE_HTML = (
    '<div id="courseList"><div class="course">'
    '<a href="/mycourse/stucoursemiddle?courseid=101&amp;clazzid=201">'
    '<span class="course-name" title="Fixture">Fixture</span></a>'
    "<p>开课时间：2026-11-06～2026-12-31</p></div></div>"
)


class FakeResponse:
    def __init__(self, *, status=200, text="", url="http://mooc1-1.chaoxing.com/visit/courselistdata"):
        self.status = status
        self.ok = 200 <= status < 400
        self.url = url
        self._text = text

    async def text(self):
        return self._text


class FakeRequest:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    async def get(self, url, **kwargs):
        self.calls.append(("get", url))
        if self.error:
            raise self.error
        return self.response

    async def post(self, url, **kwargs):
        self.calls.append(("post", url))
        if self.error:
            raise self.error
        return self.response


class FakeContext:
    def __init__(self, request):
        self.request = request


class HttpCollectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_course_list_endpoint_avoids_portal_rendering(self):
        request = FakeRequest(FakeResponse(text=COURSE_HTML))
        with patch.object(
            chaoxing, "_fetch_student_course_list_from_portal", new=AsyncMock()
        ) as portal:
            url, html = await chaoxing._fetch_course_list_html(
                FakeContext(request), object(), 3
            )
        portal.assert_not_awaited()
        self.assertEqual(request.calls, [("post", chaoxing.COURSE_LIST_DATA_URL)])
        self.assertIn("course-name", html)
        self.assertEqual(len(chaoxing._parse_courses(html, url)), 1)

    async def test_course_list_endpoint_failure_falls_back_to_portal(self):
        request = FakeRequest(FakeResponse(status=503, text="unavailable"))
        fallback = AsyncMock(return_value=("https://portal.test", COURSE_HTML))
        with patch.object(
            chaoxing, "_fetch_student_course_list_from_portal", new=fallback
        ):
            url, html = await chaoxing._fetch_course_list_html(
                FakeContext(request), object(), 3
            )
        fallback.assert_awaited_once()
        self.assertEqual(url, "https://portal.test")
        self.assertEqual(len(chaoxing._parse_courses(html, url)), 1)

    async def test_course_list_endpoint_login_page_is_treated_as_failure(self):
        request = FakeRequest(
            FakeResponse(text=COURSE_HTML, url="https://passport2.chaoxing.com/login")
        )
        fallback = AsyncMock(return_value=("https://portal.test", COURSE_HTML))
        with patch.object(
            chaoxing, "_fetch_student_course_list_from_portal", new=fallback
        ):
            await chaoxing._fetch_course_list_html(FakeContext(request), object(), 3)
        fallback.assert_awaited_once()

    async def test_page_html_prefers_http_without_rendering(self):
        request = FakeRequest(FakeResponse(text="<html>course page</html>"))
        with patch.object(chaoxing, "_fetch_rendered_page", new=AsyncMock()) as render:
            url, html = await chaoxing._fetch_page_html(
                FakeContext(request), object(), "https://mooc2-ans.test/course"
            )
        render.assert_not_awaited()
        self.assertEqual(html, "<html>course page</html>")

    async def test_page_html_falls_back_to_rendering_on_http_error(self):
        request = FakeRequest(FakeResponse(status=500, text=""))
        rendered = AsyncMock(return_value=("https://rendered.test", "<html>rendered</html>"))
        with patch.object(chaoxing, "_fetch_rendered_page", new=rendered):
            url, html = await chaoxing._fetch_page_html(
                FakeContext(request), object(), "https://mooc2-ans.test/course"
            )
        rendered.assert_awaited_once()
        self.assertEqual(url, "https://rendered.test")

    async def test_page_html_does_not_fall_back_when_session_expired(self):
        request = FakeRequest(
            FakeResponse(status=200, text="<html></html>", url="https://passport2.chaoxing.com/login")
        )
        with patch.object(chaoxing, "_fetch_rendered_page", new=AsyncMock()) as render:
            with self.assertRaisesRegex(RuntimeError, "登录态已失效"):
                await chaoxing._fetch_page_html(
                    FakeContext(request), object(), "https://mooc2-ans.test/course"
                )
        render.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
