"""隔离验证设置整合和本地更新日志弹窗，不读取真实账户。"""
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=True)
    page = browser.new_page(viewport={"width": 1222, "height": 916})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/api/preferences", lambda route: route.fulfill(json={
        "assignments": {"max_steps": 100, "concurrency": 1},
        "learning": {"headless": True, "playback_rate": 1, "volume": 0, "resource_wait_seconds": 15, "confirmation_wait_seconds": 25, "stall_seconds": 60},
        "system": {"collection_concurrency": 2, "log_retention_days": 14, "log_size_mb": 200},
    }))
    page.route("http://127.0.0.1:5174/settings-changelog-test", lambda route: route.fulfill(content_type="text/html", body='<div id="test"></div>'))
    page.goto("http://127.0.0.1:5174/settings-changelog-test")
    page.evaluate("""async () => {
      await import('/src/styles/settings.css');
      const {createApp,h,ref} = await import('/node_modules/.vite/deps/vue.js');
      const {default: Dialog} = await import('/src/features/settings/components/SettingsDialog.vue');
      const section=ref('system');
      createApp({setup:()=>()=>h(Dialog,{open:true,section:section.value,'onUpdate:section':v=>section.value=v,
        profile:{nickname:'演示用户'},saveProfile:async()=>true,update:{status:'idle'},
        autoCheck:false,setAutoCheck:()=>{},checkUpdates:()=>{},openExternal:()=>{},openData:()=>{},openLogs:()=>{}})}).mount('#test');
    }""")
    page.locator("#application-settings-title").wait_for()
    assert page.get_by_role("button", name="常用", exact=True).count() == 0
    assert page.get_by_role("switch", name="开机自启").count() == 1
    assert page.get_by_role("switch", name="启动时自动检查更新").count() == 1
    assert page.get_by_text("自动检测并发").count() == 1
    assert page.get_by_role("button", name="系统", exact=True).get_attribute("aria-current") == "page"
    page.get_by_role("searchbox", name="搜索设置").fill("备份")
    page.get_by_role("button", name="数据", exact=True).click()
    assert page.locator("#application-settings-title").inner_text() == "数据"
    page.get_by_role("searchbox", name="搜索设置").fill("")
    page.get_by_role("button", name="关于", exact=True).click()
    page.get_by_role("button", name="查看更新日志").click()
    modal = page.get_by_role("dialog", name="更新日志")
    modal.wait_for()
    assert modal.get_by_role("option", name="v0.9.1").count() == 1
    assert modal.get_by_text("新功能").count() > 0
    assert modal.get_by_text("模型页新增「模型 / 统计」", exact=False).count() == 0
    page.keyboard.press("Escape")
    assert modal.count() == 0
    assert page.get_by_role("button", name="MIT · 无担保").count() == 1
    assert page.locator("nav.about-links button").count() == 2
    assert not errors, errors
    browser.close()
    print("系统设置整合、更新日志弹窗及许可位置检查通过")
