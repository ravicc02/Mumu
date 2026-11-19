"""隔离验证模型优先级视觉布局和编辑交互，不连接真实服务。"""
import json
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=True)
    page = browser.new_page(viewport={"width": 1222, "height": 916})
    errors, writes = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    config = {"providers": [{"id": "demo", "name": "演示供应商", "base_url": "https://example.test/v1", "api_key_configured": True,
                            "models": ["gpt-6-sol", "gpt-6-astra", "gpt-6.1-terra", "gpt-6.1-sol", "gpt-6.2-sol"], "api_format": "openai"}]}
    def api(route):
        if route.request.method != "GET": writes.append(route.request.url)
        route.fulfill(content_type="application/json", body=json.dumps(config))
    page.route("**/api/**", api)
    page.route("http://127.0.0.1:5174/model-priority-test", lambda route: route.fulfill(content_type="text/html", body='<html><body style="margin:0"><div id="test" style="width:860px;margin:20px auto"></div></body></html>'))
    page.goto("http://127.0.0.1:5174/model-priority-test")
    page.evaluate("""async () => {
      await import('/src/styles/settings.css'); await import('/src/styles/workbench.css');
      const {createApp,h} = await import('/node_modules/.vite/deps/vue.js');
      const {default:Model} = await import('/src/features/settings/components/ModelConfiguration.vue');
      createApp({render:()=>h(Model)}).mount('#test');
    }""")
    rows = page.locator(".priority-list li")
    rows.first.wait_for()
    assert rows.count() == 5
    assert rows.first.get_by_text("主模型").count() == 1
    assert rows.nth(4).get_by_text("备用 4").count() == 1
    assert page.locator(".model-priority-box").is_visible()
    assert page.get_by_role("button", name="从服务商拉取模型列表").is_visible()
    assert page.get_by_role("button", name="测试连接").is_visible()
    page.get_by_role("button", name="编辑 gpt-6-sol").click()
    page.get_by_role("textbox", name="编辑 gpt-6-sol").fill("gpt-6-new")
    page.get_by_role("button", name="确认编辑 gpt-6-sol").click()
    assert rows.first.get_by_text("gpt-6-new").count() == 1
    page.get_by_role("button", name="添加模型", exact=True).click()
    page.get_by_role("textbox", name="模型名称").fill("another-model")
    page.get_by_role("button", name="添加", exact=True).click()
    assert rows.count() == 6
    for width in (1182, 1222, 1440):
        page.set_viewport_size({"width": width, "height": 916})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
    assert not writes, writes
    assert not errors, errors
    browser.close()
    print("模型优先级列表、编辑添加、操作入口及无溢出检查通过")
