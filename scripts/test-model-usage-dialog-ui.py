"""在完整设置弹窗只读验证统计，不写真实配置。"""
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=True)
    context = browser.new_context(viewport={"width": 1222, "height": 916})
    context.grant_permissions(['local-network-access'], origin='http://127.0.0.1:5174')
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on('requestfailed', lambda r: print('网络请求失败', r.url, r.failure))
    page.on('console', lambda m: print('浏览器错误', m.text) if m.type == 'error' else None)
    page.route("**/api/model-config", lambda r: r.fulfill(json={"providers": [{"id": "demo", "name": "演示供应商", "models": ["demo-model"], "api_key_configured": True}]}))
    page.route("http://127.0.0.1:5174/usage-dialog-test", lambda r: r.fulfill(content_type="text/html", body='<div id="test"></div>'))
    page.goto("http://127.0.0.1:5174/usage-dialog-test")
    page.evaluate('''async () => {
      await import('/src/styles/workbench.css');
      await import('/src/styles/settings.css');
      const {createApp,h,ref} = await import('/node_modules/.vite/deps/vue.js');
      const {default:Dialog} = await import('/src/features/settings/components/SettingsDialog.vue');
      const section=ref('model');
      createApp({setup:()=>()=>h(Dialog,{open:true,section:section.value,'onUpdate:section':v=>section.value=v,profile:{nickname:'演示用户'},saveProfile:async()=>true,update:{status:'idle'},autoCheck:false,setAutoCheck:()=>{},checkUpdates:()=>{},openExternal:()=>{},openData:()=>{},openLogs:()=>{}})}).mount('#test');
    }''')
    page.get_by_role("tab", name="统计", exact=True).click()
    try:
        page.locator('.usage-metrics').wait_for(timeout=10000)
    except Exception:
        print(page.locator('.model-usage').inner_text(), errors)
        raise
    for width in (1182, 1222, 1440):
        page.set_viewport_size({"width": width, "height": 916})
        for label in ("请求日志", "供应商统计", "模型统计", "工具统计", "定价配置"):
            page.get_by_role('tab', name=label, exact=True).click()
            page.get_by_role('status').filter(has_text='正在读取用量').wait_for(state='hidden')
            assert page.get_by_role('alert').count() == 0
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert page.locator('.usage-tabs').evaluate('(el)=>el.scrollWidth<=el.clientWidth+1')
        page.get_by_role('tab', name='请求日志', exact=True).click()
        page.locator('.usage-requests').wait_for()
    assert not errors, errors
    browser.close()
    print("完整设置弹窗：五个统计分页、真实只读接口、三种PC宽度和控制台检查通过")
