"""隔离挂载界面，验证视图切换与设置，不操作真实账号。"""
from pathlib import Path
import json
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
with sync_playwright() as p:
    browser = p.chromium.launch(channel='chrome', headless=True)
    page = browser.new_page(viewport={'width': 1182, 'height': 916})
    errors, writes = [], []
    page.on('pageerror', lambda e: errors.append(str(e)))
    config = {'reasoning_effort': 'high', 'providers': [{'id': 'demo', 'name': '演示服务', 'base_url': 'https://example.com/v1', 'api_key_configured': True, 'models': ['demo-model'], 'api_format': 'openai'}]}
    def api(route):
        request = route.request
        if request.method != 'GET': writes.append(request.url)
        route.fulfill(content_type='application/json', body=json.dumps(config if request.url.endswith('model-config') else {'reasoning_effort': 'low'}))
    page.route('**/api/**', api)
    page.route('http://127.0.0.1:5174/refinement-test', lambda r: r.fulfill(content_type='text/html', body='<html><body><div id="test"></div></body></html>'))
    page.goto('http://127.0.0.1:5174/refinement-test')
    page.wait_for_load_state('networkidle')
    page.evaluate('''async () => {
      await import('/src/styles/settings.css'); await import('/src/styles/workbench.css');
      const {createApp, h} = await import('/node_modules/.vite/deps/vue.js');
      const {default: Town} = await import('/src/features/task-town/components/TaskTown.vue');
      const {default: Model} = await import('/src/features/settings/components/ModelConfiguration.vue');
      const {default: Slider} = await import('/src/features/settings/components/ReasoningSlider.vue');
      window.app = createApp({render:()=>h('div',{},[
        h(Town,{ready:true,online:true,tasks:[{id:'demo',account_id:'a',assignment_title:'演示任务',account_name:'演示账号',status:'running'}]}),
        h(Model),h(Slider)
      ])}); app.mount('#test');
    }''')
    page.get_by_role('button', name='切换 3D 办公室').wait_for()
    assert page.locator('.town-canvas').count() == 0
    page.get_by_role('button', name='切换 3D 办公室').click()
    page.locator('.town-canvas[data-screen-frames]').wait_for(timeout=30000)
    page.wait_for_timeout(500)
    first = page.locator('.town-canvas').screenshot()
    page.locator('.town-canvas').focus()
    page.keyboard.down('w'); page.wait_for_timeout(350); page.keyboard.up('w')
    assert page.locator('.town-canvas').screenshot() != first
    from PIL import Image
    from io import BytesIO
    pixels = Image.open(BytesIO(page.locator('.town-canvas').screenshot())).convert('RGB')
    assert len(pixels.getcolors(pixels.width * pixels.height)) > 100
    page.get_by_role('button', name='全景视角').click()
    page.get_by_role('button', name='切换普通面板').click()
    assert page.locator('.town-canvas').count() == 0
    assert not writes, writes
    slider = page.get_by_role('slider')
    slider.focus(); slider.press('ArrowLeft'); slider.press('Tab')
    page.get_by_text('已保存', exact=True).wait_for()
    assert len(writes) == 1 and writes[0].endswith('/model-config/reasoning'), writes
    for width in (1182, 1440):
        page.set_viewport_size({'width': width, 'height': 916})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.evaluate('''async () => {
      app.unmount();
      const {createApp,h,ref} = await import('/node_modules/.vite/deps/vue.js');
      const {default: Dialog} = await import('/src/features/settings/components/SettingsDialog.vue');
      const section=ref('model');
      window.settingsApp=createApp({setup:()=>()=>h(Dialog,{open:true,section:section.value,'onUpdate:section':v=>section.value=v,profile:{nickname:'演示用户',email:'demo@example.com'},saveProfile:async()=>true,update:{},openData:async()=>true,openLogs:async()=>true})});
      settingsApp.mount('#test');
    }''')
    page.get_by_role('heading', name='模型', exact=True).wait_for()
    page.locator('.model-configuration input[type=url]').wait_for()
    page.screenshot(path=str(ROOT / 'docs/assets/workspace-refinement-test.png'), full_page=True)
    page.get_by_role('button', name='账户', exact=True).click()
    assert page.locator('.local-profile-form').count() == 0 or page.locator('.local-account-settings').is_visible()
    assert page.locator('.local-profile-summary').bounding_box()['y'] < page.locator('.local-profile-editor').bounding_box()['y']
    for width in (1182,1440):
        page.set_viewport_size({'width':width,'height':916})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    assert not errors, errors
    browser.close()
    print('普通面板/3D往返、动画像素、镜头、释放、滑动保存及双尺寸检查通过')
