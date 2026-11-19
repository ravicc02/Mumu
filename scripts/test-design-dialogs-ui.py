"""授权与任务结果弹窗的隔离渲染和键盘检查。"""
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/assets/design-system'
with sync_playwright() as p:
    browser=p.chromium.launch(channel='chrome',headless=True)
    page=browser.new_page(viewport={'width':1060,'height':850})
    errors,writes=[],[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    def api(route):
        if route.request.method!='GET':writes.append(route.request.url)
        url=route.request.url.split('?')[0]
        data=[] if url.endswith(('/events','/artifacts')) else {'id':'demo','status':'interrupted','phase':'failed','course_name':'演示课程：'+('长标题'*20),'account_name':'演示账号','attempt':1,'error':'演示网络异常，请确认连接后重试','target':[],'completed':[]}
        route.fulfill(json=data)
    page.route('**/api/**',api)
    page.route('**/design-dialogs-test',lambda r:r.fulfill(content_type='text/html',body='<div id="test"></div>'))
    page.goto('http://127.0.0.1:5174/design-dialogs-test')
    page.wait_for_load_state('networkidle')
    page.evaluate('''async()=>{
      for(const name of ['workbench','settings','design-system'])await import(`/src/styles/${name}.css`);
      const {createApp,h,ref}=await import('/node_modules/.vite/deps/vue.js');
      const {default:Auth}=await import('/src/features/accounts/components/AccountAuthorization.vue');
      const {default:Result}=await import('/src/features/learning/components/LearningTaskDetails.vue');
      window.kind=ref('auth');window.authState=ref('waiting');window.open=ref(true);
      createApp({setup:()=>()=>h('div',{},[h('button',{id:'trigger',onClick:()=>open.value=true},'打开'),kind.value==='auth'?h(Auth,{open:open.value,state:{status:authState.value,platform:'chaoxing',error:'演示授权失败，请重新授权',account:{name:'演示账号'}},onClose:()=>open.value=false}):h(Result,{open:open.value,runId:'demo',onClose:()=>open.value=false})])}).mount('#test');
    }''')
    page.get_by_role('heading',name='等待授权',exact=True).wait_for()
    for state,title in [('error','授权未完成'),('success','添加成功')]:
        page.evaluate('(s)=>authState.value=s',state)
        page.get_by_role('heading',name=title,exact=True).wait_for()
        assert page.locator('.account-authorization').evaluate('(e)=>e.scrollWidth<=e.clientWidth')
        page.screenshot(path=str(OUT/f'authorization-{state}.png'))
    page.evaluate("kind.value='result'")
    page.locator('.learning-result-summary').wait_for()
    page.get_by_role('tab',name='执行日志',exact=False).click()
    page.screenshot(path=str(OUT/'task-result.png'))
    page.keyboard.press('Shift+Tab')
    assert page.locator('.learning-result-modal').evaluate('(e)=>e.contains(document.activeElement)')
    page.keyboard.press('Escape')
    page.wait_for_timeout(200)
    assert page.locator('.learning-result-modal').count()==0
    assert not errors,errors
    assert not writes,writes
    browser.close()
    print('PASS: authorization waiting/error/success; task result long-title/log-tab/Escape; no API writes')
