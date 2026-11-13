"""Capture a public product preview using isolated demonstration responses."""
import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.services.browser import chrome_executable


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:5174/')
    parser.add_argument('--out', default=str(ROOT / 'assets/screenshots/workspace.png'))
    args = parser.parse_args()
    accounts = [dict(id='readme-account-' + str(i), name='演示账号 ' + chr(65+i), platform='chaoxing', status='ready') for i in range(3)]
    tasks = []
    for i, (name, state) in enumerate([('数据库练习', 'running'), ('Python 入门', 'completed'), ('计算机基础', 'completed'), ('课程阅读', 'completed'), ('实验报告审核', 'needs_intervention'), ('补充实验素材', 'incomplete')]):
        tasks.append(dict(id='readme-task-' + str(i), assignment_id='readme-work-' + str(i), assignment_title=name, account_id=accounts[i % 3]['id'],
            account_name=accounts[i % 3]['name'], course_name='演示课程', platform='chaoxing', status=state, progress=100 if state=='completed' else 62,
            browser_available=False, browser_retained=False, actions=[], created_at='2026-11-13T02:00:00Z', updated_at='2026-11-13T02:00:00Z',
            phase_label='执行中' if state=='running' else '已完成' if state=='completed' else '等待处理'))
    version=(ROOT/'VERSION').read_text(encoding='utf-8').strip()
    catalog=json.loads((ROOT/'backend/app/services/platforms/catalog.json').read_text(encoding='utf-8'))
    writes=[]; errors=[]
    def intercept(route):
        endpoint=urlsplit(route.request.url).path
        if route.request.method=='OPTIONS':
            route.fulfill(status=200,headers={'Access-Control-Allow-Origin':'*','Access-Control-Allow-Headers':'Content-Type','Access-Control-Allow-Methods':'GET,POST,OPTIONS'}); return
        if route.request.method!='GET':
            writes.append(endpoint); route.fulfill(status=409,json={'detail':'Preview does not perform actions'},headers={'Access-Control-Allow-Origin':'*'}); return
        value=[]
        if endpoint=='/api/health': value={'ok':True,'version':version,'model':'demo-model','ai_configured':True,'data_root':'Demo data'}
        elif endpoint=='/api/accounts': value=accounts
        elif endpoint=='/api/platforms': value=catalog
        elif endpoint=='/api/jobs': value=tasks
        elif endpoint=='/api/preferences': value={'assignments':{'auto_submit':False}}
        elif endpoint=='/api/settings': value={'data_root':'Demo data','logs_root':'Demo logs','models':['demo-model'],'api_key_set':False}
        elif endpoint.startswith('/api/jobs/') and not endpoint.endswith(('/events','/artifacts')): value=next((item for item in tasks if item['id']==endpoint.rsplit('/',1)[-1]),{})
        route.fulfill(json=value,headers={'Access-Control-Allow-Origin':'*'})
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(headless=True,executable_path=str(chrome_executable()))
        page=browser.new_page(viewport={'width':1651,'height':919},device_scale_factor=1)
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.route('**/api/**',intercept)
        page.route_web_socket('**/ws/**',lambda ws:None)
        page.goto(args.url,wait_until='networkidle')
        page.wait_for_function('document.querySelector(".town-canvas")?.dataset.playerX !== undefined')
        page.wait_for_timeout(1300)
        if errors or writes: raise RuntimeError('Preview was not isolated: '+repr({'errors':errors,'writes':writes}))
        target=Path(args.out); target.parent.mkdir(parents=True,exist_ok=True)
        page.screenshot(path=str(target))
        browser.close()
        print('Sanitized demonstration preview: '+str(target))


if __name__=='__main__': main()
