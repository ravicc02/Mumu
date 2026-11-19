"""完整应用入口的隔离 UI 验证，所有 API 均由演示响应接管。"""
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs/assets/design-system'
OUT.mkdir(parents=True, exist_ok=True)
ACCOUNT = {'id':'demo','name':'演示账号','platform':'chaoxing','status':'ready','user_id':'demo','capabilities':{'assignments':True,'learning':True,'attendance':True}}
with sync_playwright() as p:
    browser = p.chromium.launch(channel='chrome', headless=True)
    page = browser.new_page(viewport={'width':1440,'height':960})
    errors, writes = [], []
    page.on('pageerror', lambda e: errors.append(str(e)))
    def respond(route):
        path = urlparse(route.request.url).path
        if route.request.method != 'GET':
            writes.append(path)
            route.fulfill(json={}); return
        data = []
        if path == '/api/health': data = {'ok':True,'ai_configured':True,'data_root':'C:/demo/.mumu'}
        elif path == '/api/settings': data = {'data_root':'C:/demo/.mumu','version':'0.9.1'}
        elif path == '/api/onboarding': data = {'completed':True}
        elif path == '/api/accounts': data = [ACCOUNT]
        elif path == '/api/platforms': data = [{'name':'chaoxing','label':'学习通','capabilities':ACCOUNT['capabilities']}]
        elif path == '/api/connectors/weixin': data = {'configured':True,'preferences':{},'status':'online'}
        elif path == '/api/agents/channel-users': data = []
        elif path == '/api/preferences': data = {'system':{},'assignments':{},'learning':{}}
        elif path == '/api/semesters': data = []
        elif path == '/api/assignments': data = [{'id':'a1','account_id':'demo','title':'演示作业：计算机网络与数据结构综合练习','course':'演示课程','course_name':'演示课程','status':'unfinished','submittable':True,'question_count':5,'processable':True,'processing_status':'ready','assessment':{'policy_version':3,'execution_state':'direct_fill','automation_supported':True,'answer_state':'empty'}}]
        elif path == '/api/learning/courses': data = [{'id':'c1','account_id':'demo','platform':'chaoxing','name':'演示网课：操作系统基础','semester':'2026 秋季','class_id':'demo'}]
        elif path == '/api/model-config': data = {'providers':[{'id':'demo','name':'演示服务商','models':['demo-model'],'api_key_configured':True}],'reasoning_effort':'high'}
        elif path == '/api/model-usage': data = {'summary':{'requests':0,'succeeded':0,'errors':0,'usage_requests':0,'cache_requests':0},'providers':[],'models':[],'records':[],'by_provider':[],'by_model':[],'total':0}
        elif path == '/api/model-usage/tools': data = {'summary':{},'records':[],'groups':[],'tools':[],'total':0}
        elif path == '/api/model-usage/pricing': data = {'rules':[]}
        elif path == '/api/data/backups': data = []
        elif path == '/api/data/backup-policy': data = {'frequency':'off','keep':3}
        elif path == '/api/data/stats': data = {'sizes':{},'counts':{}}
        elif '/attendance/' in path: data = [] if any(x in path for x in ['groups','batches']) else {}
        elif path.endswith('/preview'): data = {'status':'idle'}
        route.fulfill(json=data)
    page.route('**/api/**', respond)
    page.route('https://api.github.com/**', lambda r:r.fulfill(status=503,json={}))
    page.add_init_script("localStorage.setItem('studio:account',JSON.stringify('demo')); localStorage.setItem('mumu:auto-check-updates','false');")
    page.goto('http://127.0.0.1:5174')
    page.wait_for_load_state('networkidle')
    page.locator('.app-shell').wait_for(state='visible',timeout=20000)
    nav = page.get_by_role('navigation',name='主导航')
    for name, selector, image in [('作业','.assignment-board','assignments'),('网课','.learning-workspace','learning'),('签到','.attendance-workspace','attendance')]:
        nav.get_by_role('button',name=name,exact=True).click()
        page.locator(selector).wait_for()
        page.wait_for_timeout(350)
        for width in (1060,1182,1440):
            page.set_viewport_size({'width':width,'height':960})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), (name,width)
            bounds=page.locator(selector).bounding_box()
            assert bounds['x']+bounds['width']<=width+1,(name,width,bounds)
        page.screenshot(path=str(OUT / f'{image}.png'))
        if name == '作业':
            page.locator('.assignment-row').wait_for()
            page.get_by_role('button',name='画面与日志',exact=True).click()
            page.locator('.inspector-bar').wait_for()
            page.get_by_role('tab',name='执行日志',exact=False).click()
            page.screenshot(path=str(OUT / 'assignment-inspector.png'))
            page.get_by_role('button',name='收起画面与日志',exact=True).click()
            assert page.locator('.inspector-bar').count()==0
    nav.get_by_role('button',name='连接器',exact=True).click()
    page.locator('.connector-card').click()
    page.locator('.modal-weixin').wait_for()
    page.get_by_role('button',name='完成',exact=True).wait_for()
    assert page.locator('.modal-weixin').evaluate('(e)=>e.scrollWidth<=e.clientWidth')
    page.screenshot(path=str(OUT / 'connector.png'))
    page.get_by_role('button',name='完成',exact=True).click()
    page.get_by_role('button',name='打开设置',exact=True).click()
    for section, selector in [('账户','.local-account-settings'),('系统','.settings-content-sections'),('作业','.settings-content-sections'),('网课','.settings-content-sections'),('签到','.settings-content-sections'),('模型','.model-configuration'),('数据','.data-settings'),('关于','.about-settings')]:
        page.locator('.application-settings-sidebar').get_by_role('button',name=section,exact=True).click()
        page.wait_for_timeout(350)
        if section=='模型':
            page.get_by_role('tab',name='统计',exact=True).click()
            page.locator('.usage-metrics').wait_for()
            assert page.locator('.application-settings-title-icon').count()==0
            assert page.get_by_text('仅统计 Mumu 本地调用台账',exact=False).count()==0
            for width in (1060,1182,1440):
                page.set_viewport_size({'width':width,'height':960})
                rects=page.locator('.usage-metrics section').evaluate_all('(es)=>es.map(e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height}})')
                assert max(r['y'] for r in rects)-min(r['y'] for r in rects)<1,rects
                assert max(r['h'] for r in rects)-min(r['h'] for r in rects)<1,rects
                tabs=page.locator('.model-subtabs').bounding_box()
                period=page.locator('.usage-toolbar').bounding_box()
                assert abs((tabs['y']+tabs['height']/2)-(period['y']+period['height']/2))<1, (width,tabs,period)
                left=page.locator('.usage-metrics').bounding_box()['x']
                panel=page.locator('.usage-query-panel').bounding_box()
                assert abs(panel['x']-left)<1
                assert abs(page.locator('.usage-tabs').bounding_box()['x']-left-21)<1
                assert abs(page.locator('.usage-table-wrap').bounding_box()['x']-left-21)<1
                assert page.locator('.application-settings-dialog').evaluate('(e)=>e.scrollWidth<=e.clientWidth+1')
                page.screenshot(path=str(OUT / f'usage-alignment-{width}.png'))
            for label in ('请求日志','供应商统计','模型统计','工具统计','定价配置'):
                page.get_by_role('tab',name=label,exact=True).click()
                page.wait_for_timeout(120)
                assert page.locator('.usage-tabs').evaluate('(el)=>el.scrollWidth<=el.clientWidth+1')
        for width in (1060,1182,1440):
            page.set_viewport_size({'width':width,'height':960})
            assert page.locator('.application-settings-dialog').evaluate('(el)=>el.scrollWidth<=el.clientWidth+1'),section
            assert page.locator('.application-settings-content').evaluate('(el)=>el.scrollWidth<=el.clientWidth+1'),(section,width)
        page.screenshot(path=str(OUT / f'section-{section}.png'))
    assert not writes,writes
    assert not errors,errors
    browser.close()
    print('完整入口：作业、网课、签到及模型统计/数据/关于通过；三种PC宽度，无API写入，无页面异常')
