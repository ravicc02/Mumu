"""使用隔离组件与演示响应验收设计规范，不连接真实账号。"""
from pathlib import Path
import json
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs/assets/design-system'
OUT.mkdir(parents=True, exist_ok=True)
with sync_playwright() as p:
    browser = p.chromium.launch(channel='chrome', headless=True)
    page = browser.new_page(viewport={'width': 1440, 'height': 960})
    errors, writes = [], []
    page.on('pageerror', lambda e: errors.append(str(e)))
    def api(route):
        if route.request.method != 'GET': writes.append(route.request.url)
        url = route.request.url
        data = {'providers': [], 'reasoning_effort': 'high'} if 'model-config' in url else {}
        route.fulfill(content_type='application/json', body=json.dumps(data))
    page.route('**/api/**', api)
    page.route('**/design-system-test', lambda r: r.fulfill(content_type='text/html', body='<html><body><div id="test" style="height:100%"></div></body></html>'))
    page.goto('http://127.0.0.1:5174/design-system-test')
    page.wait_for_load_state('networkidle')
    page.evaluate('''async () => {
      for (const name of ['workbench','settings','design-system']) await import(`/src/styles/${name}.css`);
      const {createApp,h,ref} = await import('/node_modules/.vite/deps/vue.js');
      const {default:Town} = await import('/src/features/task-town/components/TaskTown.vue');
      const {default:Platform} = await import('/src/features/platforms/components/PlatformWorkspace.vue');
      const {default:Dialog} = await import('/src/features/settings/components/SettingsDialog.vue');
      const view=ref('town'), section=ref('model'), open=ref(false);
      const platforms=[{name:'chaoxing',label:'学习通',capabilities:{assignments:true,learning:true,attendance:true}},{name:'educoder',label:'头歌',capabilities:{assignments:true}}];
      const accounts=[{id:'demo',name:'演示账号 · 长名称布局验证',platform:'chaoxing',status:'ready',last_login_at:'2026-11-19T12:00:00',capabilities:{assignments:true,learning:true}}];
      const tasks=['running','needs_intervention','completed','interrupted','paused','incomplete'].map((status,i)=>({id:String(i),account_id:'demo',account_name:'演示账号',platform:'chaoxing',assignment_title:'演示任务 · 数据结构与算法实验 '+(i+1),status}));
      window.app=createApp({setup:()=>()=>h('div',{class:'app-shell'},[
        h('header',{class:'topbar'},[h('div',{class:'brand'},[h('h1','Mumu')]),h('span',{class:'topbar-breadcrumb'},'工作空间'),h('button',{class:'button light',onClick:()=>open.value=true},'打开设置')]),
        h('div',{class:'app-body'},[h('nav',{class:'side-nav'},['town','platform'].map((id,i)=>h('button',{class:['nav-item',{active:view.value===id}],onClick:()=>view.value=id},i?'平台':'面板'))),
          view.value==='town'?h('main',{class:'dashboard-main',style:'grid-column:2;grid-row:2;display:flex;flex-direction:column;padding:0'},[h('header',{class:'page-heading',style:'padding:24px;margin:0'},[h('h2','总控中心')]),h(Town,{ready:true,online:true,tasks})]):h(Platform,{platforms,accounts,modelValue:'chaoxing'})]),
        h(Dialog,{open:open.value,section:section.value,'onUpdate:section':v=>section.value=v,onClose:()=>open.value=false,profile:{nickname:'演示用户',email:'demo@example.com'},saveProfile:async()=>true,update:{},openData:async()=>true,openLogs:async()=>true})
      ])}); app.mount('#test');
    }''')
    page.get_by_role('heading', name='总控中心').wait_for()
    for width in (1060, 1182, 1440, 1920):
        page.set_viewport_size({'width':width,'height':960})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
    page.set_viewport_size({'width':1440,'height':960})
    page.screenshot(path=str(OUT / 'dashboard.png'))
    page.get_by_role('button', name='平台', exact=True).click()
    page.locator('.platform-account-table').wait_for()
    for width in (1060, 1182, 1440):
        page.set_viewport_size({'width':width,'height':960})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
        assert page.locator('.platform-records').evaluate('(el)=>el.scrollWidth<=el.clientWidth'), width
    page.screenshot(path=str(OUT / 'platform.png'))
    page.get_by_role('button', name='打开设置', exact=True).click()
    page.get_by_role('heading', name='模型', exact=True).wait_for()
    page.screenshot(path=str(OUT / 'settings.png'))
    radius = page.locator('.button.dark').first.evaluate('(el)=>getComputedStyle(el).borderRadius')
    assert radius == '9999px', radius
    for width in (1060, 1182, 1440):
        page.set_viewport_size({'width':width,'height':960})
        assert page.locator('.application-settings-dialog').evaluate('(el)=>el.scrollWidth<=el.clientWidth'), width
    page.get_by_role('button',name='账户',exact=True).click()
    page.locator('.local-account-settings').wait_for()
    page.screenshot(path=str(OUT / 'account.png'))
    assert not writes, writes
    assert not errors, errors
    print(json.dumps({'passed':True,'widths':[1060,1182,1440,1920],'console_errors':errors,'api_writes':writes,'screenshots':str(OUT)},ensure_ascii=False))
    browser.close()
