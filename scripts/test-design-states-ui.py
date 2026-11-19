"""隔离检查启动、初始化、长列表、3D 和弹窗键盘，不执行真实业务。"""
from pathlib import Path
from io import BytesIO
from PIL import Image
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/assets/design-system'
OUT.mkdir(parents=True,exist_ok=True)
with sync_playwright() as p:
    browser=p.chromium.launch(channel='chrome',headless=True)
    page=browser.new_page(viewport={'width':1182,'height':916})
    errors,writes=[],[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    def api(route):
        if route.request.method!='GET': writes.append(route.request.url)
        url=route.request.url
        data={'status':'ready','backendReady':True,'items':[{'id':'browser','status':'ready'},{'id':'runtime','status':'ready'}]} if 'setup/environment' in url else {'providers':[],'reasoning_effort':'high'} if 'model-config' in url else {}
        route.fulfill(json=data)
    page.route('**/api/**',api)
    page.route('**/design-states-test',lambda r:r.fulfill(content_type='text/html',body='<div id="test" style="height:100%"></div>'))
    page.goto('http://127.0.0.1:5174/design-states-test')
    page.wait_for_load_state('networkidle')
    page.evaluate('''async()=>{
      for(const name of ['workbench','settings','mascot','design-system'])await import(`/src/styles/${name}.css`);
      const {createApp,h,ref}=await import('/node_modules/.vite/deps/vue.js');
      const {default:Launch}=await import('/src/features/brand/components/LaunchExperience.vue');
      const {default:Startup}=await import('/src/features/brand/components/StartupExperience.vue');
      const {default:Town}=await import('/src/features/task-town/components/TaskTown.vue');
      const {default:Dialog}=await import('/src/features/settings/components/SettingsDialog.vue');
      window.state=ref('launch');window.failed=ref(false);window.online=ref(true);window.open=ref(false);window.count=ref(80);
      const statuses=['running','needs_intervention','interrupted','incomplete','paused','completed'];
      window.app=createApp({setup:()=>()=>h('div',{style:'height:100%;display:flex;flex-direction:column'},[
        state.value==='launch'?h(Launch,{progress:42,error:failed.value?'演示：本地服务连接失败，请重试':''}):state.value==='startup'?h(Startup,{canClose:true,scope:'demo'}):h(Town,{ready:true,online:online.value,tasks:Array.from({length:count.value},(_,i)=>({id:String(i),account_id:'demo',account_name:'演示账号',platform:'chaoxing',assignment_title:'演示长标题'.repeat(15)+i,status:statuses[i%6]}))}),
        state.value==='town'?h('button',{id:'settings-trigger',class:'button light',onClick:()=>open.value=true},'打开设置'):null,
        h(Dialog,{open:open.value,section:'account',onClose:()=>open.value=false,profile:{nickname:'演示用户'},saveProfile:async()=>true,update:{}})
      ])});app.mount('#test');
    }''')
    page.locator('[role=progressbar]').wait_for()
    page.evaluate('failed.value=true')
    page.get_by_role('button',name='重新启动').wait_for()
    page.screenshot(path=str(OUT/'launch-error.png'))
    page.emulate_media(reduced_motion='reduce')
    assert page.locator('.launch-progress > span').evaluate('(e)=>getComputedStyle(e).transitionDuration')=='0s'
    page.evaluate("state.value='startup'")
    page.get_by_text('浏览器与运行库已就绪',exact=True).wait_for()
    for width in (1060,1440):
        page.set_viewport_size({'width':width,'height':916})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    assert page.locator('.startup-experience').evaluate('(e)=>getComputedStyle(e).animationName')=='none'
    assert page.locator('.startup-header nav button.active').evaluate('(e)=>getComputedStyle(e).borderRadius')=='9999px'
    page.screenshot(path=str(OUT/'onboarding.png'))
    page.get_by_role('button',name='下一步',exact=True).click()
    page.get_by_role('heading',name='连接模型服务',exact=True).wait_for()
    page.get_by_role('button',name='稍后配置',exact=False).click()
    page.get_by_role('heading',name='授权学习平台',exact=True).wait_for()
    page.screenshot(path=str(OUT/'onboarding-platforms.png'))
    page.evaluate("state.value='town'")
    page.locator('.town-panel-task').first.wait_for()
    assert page.locator('.town-panel-task').count()==80
    assert page.locator('.town-panel-list').first.evaluate('(e)=>e.scrollHeight>e.clientHeight')
    page.screenshot(path=str(OUT/'long-task-list.png'))
    page.evaluate('online.value=false')
    assert page.locator('.town-connection.offline').count()==1
    page.evaluate('online.value=true')
    page.get_by_role('button',name='切换 3D 办公室').click()
    canvas=page.locator('.town-canvas[data-screen-frames]')
    canvas.wait_for(timeout=30000)
    page.wait_for_timeout(600)
    image=Image.open(BytesIO(canvas.screenshot())).convert('RGB')
    assert len(image.getcolors(image.width*image.height))>100
    page.emulate_media(reduced_motion='no-preference')
    first=canvas.screenshot();canvas.focus()
    page.keyboard.down('w');page.wait_for_timeout(350);page.keyboard.up('w')
    assert first!=canvas.screenshot()
    page.get_by_role('button',name='全景视角').click()
    page.evaluate('count.value=6')
    page.wait_for_timeout(250)
    page.screenshot(path=str(OUT/'company.png'))
    page.get_by_role('button',name='切换普通面板').click()
    assert page.locator('.town-canvas').count()==0
    trigger=page.locator('#settings-trigger');trigger.click()
    dialog=page.locator('.application-settings-dialog')
    dialog.wait_for()
    page.keyboard.press('Shift+Tab')
    assert dialog.evaluate('(e)=>e.contains(document.activeElement)')
    page.keyboard.press('Tab')
    assert dialog.evaluate('(e)=>e.contains(document.activeElement)')
    page.keyboard.press('Escape')
    page.wait_for_timeout(220)
    assert trigger.evaluate('(e)=>e===document.activeElement')
    assert not errors,errors
    assert not writes,writes
    browser.close()
    print('PASS: startup/error/reduced-motion/onboarding/80-tasks/offline/3D/camera/disposal/focus/Escape; no API writes')
