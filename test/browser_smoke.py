from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
DEMO_URL = "http://127.0.0.1:8787/demo/quiz.html"
CONTENT_SCRIPT = ROOT / "extension" / "content.js"
SIDEPANEL_HTML = (ROOT / "extension" / "sidepanel.html").resolve().as_uri()
ARTIFACTS = ROOT / ".runtime"
ARTIFACTS.mkdir(exist_ok=True)


def install_chrome_stub(page):
    page.evaluate(
        """
        () => {
          const runtime = { onMessage: { addListener: () => {} } };
          const chromeObject = window.chrome || {};
          Object.defineProperty(chromeObject, "runtime", {
            configurable: true,
            value: runtime
          });
          Object.defineProperty(window, "chrome", {
            configurable: true,
            value: chromeObject
          });
        }
        """
    )


def run_quiz_dom_smoke(page):
    page.goto(DEMO_URL, wait_until="networkidle")
    install_chrome_stub(page)
    page.add_script_tag(path=str(CONTENT_SCRIPT))

    question = page.evaluate(
        "() => window.__AI_QUIZ_ASSISTANT_INTERNALS__.captureQuestion({})"
    )
    assert question["type"] == "single_choice"
    assert len(question["options"]) == 4
    assert "Manifest V3" in question["stem"]

    page.evaluate(
        """
        async ({ question, answer }) => {
          return window.__AI_QUIZ_ASSISTANT_INTERNALS__.applyAnswer(question, answer, {});
        }
        """,
        {
            "question": question,
            "answer": {
                "type": "single_choice",
                "selectedIndices": [1],
                "textAnswer": ""
            }
        }
    )
    assert page.locator("input[name='question-0'][value='1']").is_checked()

    page.evaluate("() => window.__AI_QUIZ_ASSISTANT_INTERNALS__.clickNext({})")
    page.wait_for_function(
        "() => document.querySelector('.stem')?.textContent.includes('哪些')"
    )

    multiple_question = page.evaluate(
        "() => window.__AI_QUIZ_ASSISTANT_INTERNALS__.captureQuestion({})"
    )
    assert multiple_question["type"] == "multiple_choice"
    page.evaluate(
        """
        async ({ question, answer }) => {
          return window.__AI_QUIZ_ASSISTANT_INTERNALS__.applyAnswer(question, answer, {});
        }
        """,
        {
            "question": multiple_question,
            "answer": {
                "type": "multiple_choice",
                "selectedIndices": [0, 2],
                "textAnswer": ""
            }
        }
    )
    assert page.locator("input[name='question-1'][value='0']").is_checked()
    assert page.locator("input[name='question-1'][value='2']").is_checked()

    page.evaluate("() => window.__AI_QUIZ_ASSISTANT_INTERNALS__.clickNext({})")
    page.wait_for_function(
        "() => document.querySelector('.stem')?.textContent.includes('Cookie')"
    )

    text_question = page.evaluate(
        "() => window.__AI_QUIZ_ASSISTANT_INTERNALS__.captureQuestion({})"
    )
    assert text_question["type"] == "text"
    page.evaluate(
        """
        async ({ question, answer }) => {
          return window.__AI_QUIZ_ASSISTANT_INTERNALS__.applyAnswer(question, answer, {});
        }
        """,
        {
            "question": text_question,
            "answer": {
                "type": "text",
                "selectedIndices": [],
                "textAnswer": "Cookie 属于敏感凭据，上传会增加泄露和滥用风险。"
            }
        }
    )
    assert "敏感凭据" in page.locator("textarea").input_value()
    page.screenshot(path=str(ARTIFACTS / "demo-smoke.png"), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate(
        "() => document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )
    page.screenshot(path=str(ARTIFACTS / "demo-mobile-smoke.png"), full_page=True)


def run_sidepanel_visual_smoke(browser):
    context = browser.new_context(viewport={"width": 420, "height": 900})
    page = context.new_page()
    page.add_init_script(
        """
        (() => {
          const noop = () => {};
          const question = {
            signature: "demo0001",
            type: "single_choice",
            stem: "Manifest V3 扩展的后台脚本通常使用哪一种工作线程？",
            options: [
              { index: 0, text: "Service Worker", value: "0" },
              { index: 1, text: "Content Script", value: "1" },
              { index: 2, text: "Popup Page", value: "2" },
              { index: 3, text: "Renderer Thread", value: "3" }
            ],
            textEntry: null,
            pageTitle: "浏览器扩展知识测验",
            pageUrl: "http://127.0.0.1:8787/demo/quiz.html"
          };
          Object.defineProperty(window, "chrome", {
            configurable: true,
            value: {
              storage: {
                local: {
                  get: async () => ({}),
                  set: async () => {}
                }
              },
              tabs: {
                query: async (queryInfo) => queryInfo.currentWindow
                  ? [{ id: 1 }]
                  : [{
                      id: 1,
                      url: "http://127.0.0.1:8787/demo/quiz.html"
                    }],
                sendMessage: async (_tabId, message) => {
                  if (message.type === "CAPTURE_QUESTION") {
                    return { ok: true, question };
                  }
                  if (message.type === "APPLY_ANSWER") {
                    return {
                      ok: true,
                      result: {
                        applied: true,
                        kind: message.answer.type,
                        selectedIndices: message.answer.selectedIndices
                      }
                    };
                  }
                  if (message.type === "CLICK_NEXT") {
                    return { ok: true, result: { clicked: true } };
                  }
                  return { ok: false, error: "unknown message" };
                },
                onActivated: { addListener: noop },
                onUpdated: { addListener: noop }
              },
              permissions: {
                contains: async () => true,
                request: async () => true
              },
              scripting: { executeScript: async () => {} }
            }
          });
        })();
        """
    )
    page.route(
        "http://127.0.0.1:8787/api/health",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body='{"ok":true,"mode":"mock","model":"local-demo","configured":false}'
        )
    )
    page.route(
        "http://127.0.0.1:8787/api/solve",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body="""{
              "ok": true,
              "answer": {
                "type": "single_choice",
                "selectedIndices": [0],
                "textAnswer": "",
                "confidence": 0.92,
                "explanation": "Manifest V3 使用 Service Worker 接管后台事件。",
                "model": "local-demo",
                "mode": "mock"
              }
            }"""
        )
    )
    page.goto(SIDEPANEL_HTML, wait_until="networkidle")
    page.wait_for_function(
        """
        () => {
          const badge = document.querySelector("#serverBadge");
          return badge?.classList.contains("badge-live")
            || badge?.classList.contains("badge-mock")
            || badge?.classList.contains("badge-low");
        }
        """
    )
    page.get_by_role("button", name="授权并扫描").click()
    page.locator("#questionSection").wait_for(state="visible")
    page.get_by_role("button", name="获取答案").click()
    page.locator("#answerSection").wait_for(state="visible")
    page.get_by_role("button", name="填入答案").click()
    page.wait_for_function(
        "() => document.querySelector('#status')?.textContent.includes('答案已填入')"
    )
    assert page.get_by_role("button", name="下一题").is_enabled()
    page.screenshot(path=str(ARTIFACTS / "sidepanel-smoke.png"), full_page=True)
    page.set_viewport_size({"width": 320, "height": 720})
    assert page.evaluate(
        "() => document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )
    page.screenshot(path=str(ARTIFACTS / "sidepanel-mobile-smoke.png"), full_page=True)
    context.close()


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=["--disable-web-security"]
        )
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        run_quiz_dom_smoke(page)
        context.close()
        run_sidepanel_visual_smoke(browser)
        browser.close()

    print("Browser smoke test passed")
    print(ARTIFACTS / "demo-smoke.png")
    print(ARTIFACTS / "demo-mobile-smoke.png")
    print(ARTIFACTS / "sidepanel-smoke.png")
    print(ARTIFACTS / "sidepanel-mobile-smoke.png")


if __name__ == "__main__":
    main()
