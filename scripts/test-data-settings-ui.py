"""在隔离页面中验证真实数据设置组件，不连接真实账号或执行维护。"""
from pathlib import Path
import json
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=True)
    page = browser.new_page(viewport={"width": 1100, "height": 850})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/api/**", lambda route: route.fulfill(content_type="application/json", body=json.dumps(
        {"system": {"log_retention_days": 14, "log_size_mb": 200}} if route.request.url.endswith("preferences") else
        {"frequency": "off", "keep": 3} if route.request.url.endswith("backup-policy") else
        [] if route.request.url.endswith("backups") else {"sizes": {}, "counts": {}}
    )))
    page.route("http://127.0.0.1:5174/data-component-test", lambda route: route.fulfill(content_type="text/html", body='<html><head><script type="module" src="/src/main.js"></script></head><body><div id="app"></div></body></html>'))
    page.route("**/src/main.js", lambda route: route.fulfill(content_type="application/javascript", body="import '/src/styles/settings.css';"))
    page.goto("http://127.0.0.1:5174/data-component-test")
    page.evaluate("""async () => {
      const {createApp} = await import('/node_modules/.vite/deps/vue.js');
      const {default: DataSettings} = await import('/src/features/settings/components/DataSettings.vue');
      document.querySelector('#app').remove();
      const host = document.createElement('div'); host.id='data-test'; document.body.append(host);
      createApp(DataSettings, { info: {data_root:'C:/demo/.mumu'}, openData:async()=>true, openLogs:async()=>true }).mount(host);
    }""")
    page.get_by_role("heading", name="数据保留与自动备份").wait_for()
    page.get_by_role("button", name="保存设置").click()
    page.get_by_text("数据保留策略与自动备份设置已保存。").wait_for()
    for width in (1100, 1280):
        page.set_viewport_size({"width": width, "height": 850})
        overflow = page.evaluate("[...document.querySelectorAll('#data-test *')].filter(e=>e.getBoundingClientRect().right>innerWidth+1).map(e=>({tag:e.tagName,cls:e.className,right:e.getBoundingClientRect().right})).slice(0,8)")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), overflow
    assert not errors, errors
    print("数据设置真实 Vue 渲染、保存交互、双尺寸溢出与 pageerror 检查通过")
    browser.close()
