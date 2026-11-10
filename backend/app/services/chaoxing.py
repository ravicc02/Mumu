from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse, parse_qs

from bs4 import BeautifulSoup
from playwright.async_api import BrowserContext, Page, async_playwright

from ..db import Database, utc_now


LOGIN_URL = (
    "https://passport2.chaoxing.com/login"
    "?fid=0&newversion=true&refer=https%3A%2F%2Fi.chaoxing.com"
)
WORK_LIST_BASE = "https://mooc1.chaoxing.com/mooc2/work/list"

_login_jobs: dict[str, asyncio.Task[None]] = {}
_login_status: dict[str, dict[str, Any]] = {}


def login_status(account_id: str) -> dict[str, Any]:
    return _login_status.get(account_id, {"status": "idle"})


def start_login(
    database: Database,
    account_id: str,
    settings: Any,
) -> dict[str, Any]:
    existing = _login_jobs.get(account_id)
    if existing and not existing.done():
        return {"status": "running"}
    _login_status[account_id] = {"status": "starting"}
    task = asyncio.create_task(_login_account(database, account_id, settings))
    _login_jobs[account_id] = task
    return {"status": "starting"}


async def _login_account(database: Database, account_id: str, settings: Any) -> None:
    account = database.get_account(account_id)
    _login_status[account_id] = {
        "status": "waiting",
        "message": "请在打开的浏览器中完成登录",
    }
    login_context: BrowserContext | None = None
    try:
        async with async_playwright() as playwright:
            login_context = await playwright.chromium.launch_persistent_context(
                account["profile_dir"],
                headless=False,
                viewport={"width": 1360, "height": 900},
                args=["--start-maximized"],
            )
            page = (
                login_context.pages[0]
                if login_context.pages
                else await login_context.new_page()
            )
            await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
            deadline = asyncio.get_running_loop().time() + 300
            authenticated = False
            while asyncio.get_running_loop().time() < deadline:
                if await _verify_authenticated(login_context):
                    authenticated = True
                    break
                await asyncio.sleep(1.5)
            if not authenticated:
                raise TimeoutError("登录等待超时")
            await asyncio.sleep(1)
            storage_state = account["storage_state_path"]
            await login_context.storage_state(path=storage_state)
            await login_context.close()
            login_context = None
            await asyncio.sleep(0.8)

            verify_context = await playwright.chromium.launch_persistent_context(
                account["profile_dir"],
                headless=True,
            )
            try:
                await _restore_storage_state(verify_context, storage_state)
                if not await _verify_authenticated(verify_context):
                    raise RuntimeError("登录状态未能持久化，请重新登录")
            finally:
                await verify_context.close()

            database.update_account(
                account_id,
                status="ready",
                last_login_at=utc_now(),
            )
            _login_status[account_id] = {
                "status": "success",
                "message": "登录完成",
            }
    except Exception as error:
        database.update_account(account_id, status="login_error")
        _login_status[account_id] = {
            "status": "error",
            "message": str(error),
        }
    finally:
        if login_context is not None:
            try:
                await login_context.close()
            except Exception:
                pass
        _login_jobs.pop(account_id, None)


def _is_authenticated_url(url: str) -> bool:
    return "passport" not in url.lower() and "login" not in url.lower()


async def _restore_storage_state(context: BrowserContext, path: str) -> int:
    state_path = Path(path)
    if not state_path.exists():
        return 0
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    cookies = state.get("cookies") or []
    if cookies:
        await context.add_cookies(cookies)
    return len(cookies)


async def _verify_authenticated(context: BrowserContext) -> bool:
    try:
        response = await context.request.get(
            "https://i.chaoxing.com/base?ws=1",
            timeout=15000,
            fail_on_status_code=False,
        )
        if "passport" in response.url.lower():
            return False
        text = await response.text()
        return "frame_content" in text or "我的课程" in text
    except Exception:
        return False


async def _fetch_text(context: BrowserContext, url: str) -> tuple[str, str]:
    response = await context.request.get(
        url,
        timeout=45000,
        fail_on_status_code=False,
    )
    text = await response.text()
    if not response.ok:
        raise RuntimeError(f"请求失败 {response.status}: {url}")
    if "passport" in urlparse(response.url).path.lower():
        raise RuntimeError("学习通登录态已失效")
    return response.url, text


async def _fetch_rendered_page(
    page: Page,
    url: str,
    wait_ms: int = 800,
) -> tuple[str, str]:
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(wait_ms)
    if "passport" in page.url.lower():
        raise RuntimeError("学习通登录态已失效")
    return page.url, await page.content()


async def _inspect_submission_page(page: Page) -> dict[str, Any]:
    controls = await page.locator(
        "button, a, input[type='button'], input[type='submit'], [role='button']"
    ).evaluate_all(
        """
        (elements) => elements
          .filter((element) => {
            const style = getComputedStyle(element);
            const rect = element.getBoundingClientRect();
            return style.display !== 'none'
              && style.visibility !== 'hidden'
              && rect.width > 0
              && rect.height > 0
              && !element.disabled;
          })
          .map((element) => (
            element.innerText
            || element.value
            || element.getAttribute('aria-label')
            || ''
          ).trim())
        """
    )
    text = _clean(await page.locator("body").inner_text())
    deadline_match = re.search(
        r"作答时间[:：]?\s*(\d{2}-\d{2}\s+\d{2}:\d{2}\s*至\s*\d{2}-\d{2}\s+\d{2}:\d{2})",
        text,
    )
    return {
        "finalTaskUrl": page.url,
        "deadlineText": deadline_match.group(1) if deadline_match else "",
        "submittable": (
            "/work/preview" not in page.url
            and any("提交" in item for item in controls)
        ),
    }


def _clean(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _semester_for(start_date: str) -> str:
    if not start_date:
        return ""
    parsed = datetime.strptime(start_date, "%Y-%m-%d")
    if parsed.month >= 8:
        return f"{parsed.year}-{parsed.year + 1}-1"
    if parsed.month >= 2:
        return f"{parsed.year - 1}-{parsed.year}-2"
    return f"{parsed.year - 1}-{parsed.year}-1"


def _parse_courses(html: str, base_url: str) -> list[dict[str, Any]]:
    document = BeautifulSoup(html, "html.parser")
    courses: list[dict[str, Any]] = []
    seen: set[str] = set()
    for name_element in document.select("#courseList .course-name"):
        card = name_element.find_parent(class_="course")
        if card is None:
            card = name_element.find_parent(id=re.compile(r"^c_"))
        card_text = _clean(card.get_text(" ", strip=True) if card else "")
        date_match = re.search(r"开课时间：(\d{4}-\d{2}-\d{2})", card_text)
        link = card.select_one("a[href*='stucoursemiddle']") if card else None
        source = link.get("href") if link else ""
        url = urljoin(base_url, source) if source else ""
        if not url:
            continue
        query = parse_qs(urlparse(url).query)
        key = f"{query.get('courseid', [''])[0]}_{query.get('clazzid', [''])[0]}"
        if key in seen:
            continue
        seen.add(key)
        courses.append(
            {
                "courseId": query.get("courseid", [""])[0],
                "classId": query.get("clazzid", [""])[0],
                "cpi": query.get("cpi", [""])[0],
                "courseName": name_element.get("title")
                or _clean(name_element.get_text()),
                "startDate": date_match.group(1) if date_match else "",
                "ended": "课程已结束" in card_text,
                "url": url,
            }
        )
    return courses


def _hidden_value(document: BeautifulSoup, element_id: str) -> str:
    element = document.select_one(f"#{element_id}")
    return str(element.get("value", "")) if element else ""


def _build_work_url(
    document: BeautifulSoup,
    response_url: str,
    course: dict[str, Any],
) -> str:
    query = parse_qs(urlparse(response_url).query)
    course_id = _hidden_value(document, "courseid") or query.get("courseid", [""])[0]
    class_id = _hidden_value(document, "clazzid") or query.get("clazzid", [""])[0]
    cpi = _hidden_value(document, "cpi") or query.get("cpi", [""])[0]
    timestamp = _hidden_value(document, "t") or query.get("t", [""])[0]
    student_enc = _hidden_value(document, "enc") or query.get("enc", [""])[0]
    work_enc = _hidden_value(document, "workEnc")
    if not all((course_id, class_id, cpi, timestamp, student_enc, work_enc)):
        raise RuntimeError("课程页缺少作业列表参数")
    return (
        f"{WORK_LIST_BASE}?courseId={course_id}&classId={class_id}"
        f"&cpi={cpi}&ut=s&t={timestamp}&stuenc={student_enc}&enc={work_enc}"
    )


def _normalize_course_list_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.hostname != "kb.chaoxing.com":
        return url
    session_key = parse_qs(parsed.query).get("s", [""])[0]
    if not session_key:
        return url
    return (
        "https://mooc1-2.chaoxing.com/visit/interaction?s="
        + session_key
    )


def _parse_assignments(document: BeautifulSoup) -> list[dict[str, Any]]:
    assignments = []
    for row in document.select("li"):
        status_element = row.select_one(".status")
        status = _clean(status_element.get_text() if status_element else "")
        if status not in {"未交", "未完成", "已完成", "待互评"}:
            continue
        title_element = row.select_one("p.overHidden2")
        task_url = row.get("data", "")
        query = parse_qs(urlparse(task_url).query)
        title = _clean(
            title_element.get_text() if title_element else row.get("aria-label", "")
        ).split(";")[0]
        assignments.append(
            {
                "title": title,
                "status": "unfinished" if status in {"未交", "未完成"} else status,
                "statusRaw": status,
                "timeText": _clean(
                    row.select_one(".time").get_text()
                    if row.select_one(".time")
                    else ""
                ),
                "taskUrl": task_url,
                "workId": query.get("workId", [""])[0],
            }
        )
    return assignments


def _inspect_submission(document: BeautifulSoup, final_url: str) -> dict[str, Any]:
    control_texts = [
        _clean(element.get_text() or element.get("value", ""))
        for element in document.select(
            "button, a, input[type='button'], input[type='submit'], [role='button']"
        )
    ]
    has_submit = any("提交" in text for text in control_texts)
    has_start = any(
        term in text
        for text in control_texts
        for term in ("开始答题", "继续答题", "作答")
    )
    text = _clean(document.get_text(" ", strip=True))
    deadline_match = re.search(
        r"作答时间[:：]?\s*(\d{2}-\d{2}\s+\d{2}:\d{2}\s*至\s*\d{2}-\d{2}\s+\d{2}:\d{2})",
        text,
    )
    return {
        "finalTaskUrl": final_url,
        "deadlineText": deadline_match.group(1) if deadline_match else "",
        "submittable": "/work/preview" not in final_url and (has_submit or has_start),
    }


async def collect_assignments(
    database: Database,
    account_id: str,
    academic_year: int,
) -> list[dict[str, Any]]:
    account = database.get_account(account_id)
    if account["status"] != "ready":
        raise RuntimeError("账号尚未授权，请先通过“刷新 Cookie”完成学习通登录")
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            account["profile_dir"],
            headless=True,
            viewport={"width": 1440, "height": 1000},
        )
        page = await context.new_page()
        try:
            restored_cookies = await _restore_storage_state(
                context,
                account["storage_state_path"],
            )
            base_url, base_html = await _fetch_rendered_page(
                page,
                "https://i.chaoxing.com/base?ws=1",
                1800,
            )
            base_document = BeautifulSoup(base_html, "html.parser")
            frame = base_document.select_one("iframe#frame_content") or base_document.select_one(
                "iframe[src*='/visit/interaction']"
            )
            if not frame or not frame.get("src"):
                database.update_account(account_id, status="login_error")
                raise RuntimeError(
                    "学习通登录态已失效，请重新登录"
                    f"（最终地址：{base_url}，恢复 Cookie：{restored_cookies}，"
                    f"页面长度：{len(base_html)}）"
                )
            course_list_url = _normalize_course_list_url(
                urljoin(base_url, frame.get("src"))
            )
            course_list_url, course_html = await _fetch_rendered_page(
                page,
                course_list_url,
                1000,
            )
            courses = [
                course
                for course in _parse_courses(course_html, course_list_url)
                if course["startDate"].startswith(f"{academic_year}-")
                and not course["ended"]
            ]
            if not courses:
                raise RuntimeError(f"没有找到 {academic_year} 年开课的课程")

            collected: list[dict[str, Any]] = []
            for course in courses:
                try:
                    course_response_url, course_page_html = await _fetch_rendered_page(
                        page,
                        course["url"],
                        800,
                    )
                    course_document = BeautifulSoup(course_page_html, "html.parser")
                    work_url = _build_work_url(
                        course_document, course_response_url, course
                    )
                    _, work_html = await _fetch_rendered_page(
                        page,
                        work_url,
                        1000,
                    )
                    work_document = BeautifulSoup(work_html, "html.parser")
                    for assignment in _parse_assignments(work_document):
                        assignment.update(
                            {
                                "accountId": account_id,
                                "platform": "chaoxing",
                                "academicYear": academic_year,
                                "semester": _semester_for(course["startDate"]),
                                "courseName": course["courseName"],
                                "courseId": course["courseId"],
                                "classId": course["classId"],
                            }
                        )
                        if assignment["statusRaw"] in {"未交", "未完成"}:
                            final_url, _ = await _fetch_rendered_page(
                                page,
                                assignment["taskUrl"],
                                800,
                            )
                            assignment.update(await _inspect_submission_page(page))
                        else:
                            assignment.update(
                                {
                                    "finalTaskUrl": assignment["taskUrl"],
                                    "deadlineText": "",
                                    "submittable": False,
                                }
                            )
                        assignment_id = database.upsert_assignment(
                            account_id, "chaoxing", assignment, None
                        )
                        assignment["id"] = assignment_id
                        collected.append(assignment)
                except Exception:
                    continue
            database.update_account(
                account_id,
                status="ready",
                last_login_at=utc_now(),
            )
            return collected
        finally:
            await context.close()


async def refresh_session(
    database: Database,
    account_id: str,
) -> dict[str, Any]:
    account = database.get_account(account_id)
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            account["profile_dir"],
            headless=True,
            viewport={"width": 1440, "height": 1000},
        )
        try:
            await _restore_storage_state(context, account["storage_state_path"])
            if not await _verify_authenticated(context):
                database.update_account(account_id, status="login_error")
                return {
                    "ok": False,
                    "status": "login_error",
                    "message": "Cookie 已失效，请重新授权登录",
                }
            await context.storage_state(path=account["storage_state_path"])
            database.update_account(account_id, status="ready", last_login_at=utc_now())
            return {
                "ok": True,
                "status": "ready",
                "message": "Cookie 已验证并刷新",
            }
        finally:
            await context.close()
