"""统计页对齐回归：完整设置容器、隔离 API、禁止实际写入。"""
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=True)
    page = browser.new_page(viewport={"width": 1060, "height": 1010})
    errors, writes = [], []
    page.on("pageerror", lambda e: errors.append(str(e)))
    summary = dict(requests=13, succeeded=13, errors=0, usage_requests=13, total_tokens=25400, input_tokens=22000, output_tokens=3400, costs={}, priced_requests=0)
    record = dict(id="demo", created_at="2026-11-20T04:15:00+08:00", provider_name="演示供应商", model="gpt-demo-with-a-long-model-name", total_tokens=25400, input_tokens=22000, output_tokens=3400, estimated_cost=None, currency="CNY", latency_ms=8400, status="succeeded", http_status=None)
    def api(route):
        if route.request.method != "GET":
            writes.append(route.request.url)
            route.abort()
            return
        url = route.request.url
        if "model-usage" in url:
            if "/tools" in url:
                result = dict(total=0, tools=[], groups=[], records=[])
            elif "/pricing" in url:
                result = dict(rules=[])
            else:
                result = dict(summary=summary, total=13, records=[dict(record, id=str(i)) for i in range(13)], providers=[dict(id="demo", name="演示供应商")], models=[record["model"]], by_provider=[], by_model=[])
        elif "model-config" in url:
            result = dict(providers=[])
        else:
            result = dict(assignments={}, learning={}, system={})
        route.fulfill(json=result)
    page.route("**/api/**", api)
    page.route("http://127.0.0.1:5174/usage-alignment-test", lambda r: r.fulfill(content_type="text/html", body='<div id="test"></div>'))
    page.goto("http://127.0.0.1:5174/usage-alignment-test")
    page.evaluate('''async () => {
      await import('/src/styles/workbench.css');
      await import('/src/styles/settings.css');
      await import('/src/styles/design-system.css');
      const {createApp,h} = await import('/node_modules/.vite/deps/vue.js');
      const {default:Dialog} = await import('/src/features/settings/components/SettingsDialog.vue');
      createApp({render:()=>h(Dialog,{open:true,section:'model',profile:{nickname:'演示用户'},saveProfile:async()=>true,update:{status:'idle'}})}).mount('#test');
    }''')
    page.get_by_role("tab", name="统计", exact=True).click()
    page.get_by_text("共 13 条记录").wait_for()
    for width in (1060, 1182, 1440):
        page.set_viewport_size(dict(width=width, height=1010))
        page.wait_for_timeout(150)
        geometry = page.evaluate('''() => {
          const rect=s=>document.querySelector(s).getBoundingClientRect();
          const metrics=rect('.usage-metrics'), panel=rect('.usage-query-panel'), tabs=rect('.usage-tabs'), filters=rect('.usage-filters'), table=rect('.usage-table-wrap'), title=rect('#application-settings-title');
          const cards=[...document.querySelectorAll('.usage-metrics section')].map(e=>e.getBoundingClientRect());
          const inputs=[...document.querySelectorAll('.usage-filters select')].map(e=>e.getBoundingClientRect());
          return {edges:[title.x,metrics.x,panel.x], innerEdges:[tabs.x,filters.x,table.x], inset:tabs.x-panel.x, separation:panel.y-metrics.bottom, cards:cards.map(e=>({y:e.y,height:e.height,width:e.width})), controls:inputs.map(e=>({y:e.y,height:e.height})), overflow:document.documentElement.scrollWidth>innerWidth, headerBorder:getComputedStyle(document.querySelector('.application-settings-header')).borderBottomWidth};
        }''')
        assert max(geometry['edges'])-min(geometry['edges']) < 2, geometry
        assert max(geometry['innerEdges'])-min(geometry['innerEdges']) < 2, geometry
        assert geometry['inset'] >= 20 and geometry['separation'] >= 24, geometry
        assert len(set(round(c['y']) for c in geometry['cards'])) == 1, geometry
        assert max(c['width'] for c in geometry['cards'])-min(c['width'] for c in geometry['cards']) < 1, geometry
        assert len(set(round(c['height']) for c in geometry['cards'])) == 1, geometry
        assert len(set(round(c['y']) for c in geometry['controls'])) == 1, geometry
        assert len(set(round(c['height']) for c in geometry['controls'])) == 1, geometry
        assert geometry['headerBorder'] == '0px' and not geometry['overflow'], geometry
        assert page.locator('.usage-requests thead th').count() == 7
        assert page.get_by_text('仅统计 Mumu 本地调用台账', exact=False).count() == 0
        page.screenshot(path=str(ROOT / f'docs/assets/design-system/usage-alignment-{width}.png'))
        print(f'{width}px：页面与面板对齐，查询内部共线且有20px内缩、24px模块间距，指标与筛选对齐通过')
    page.get_by_label('详细记录', exact=True).check()
    page.locator('.usage-detail-row').first.wait_for()
    assert page.locator('.usage-detail-row td').first.get_attribute('colspan') == '7'
    for label in ('供应商统计', '模型统计', '工具统计', '定价配置'):
        page.get_by_role('tab', name=label, exact=True).click()
        page.get_by_role('status').filter(has_text='正在读取用量').wait_for(state='hidden')
        assert page.get_by_role('alert').count() == 0
    assert not errors and not writes, (errors, writes)
    browser.close()
    print('五分类、详情跨列、无页面异常、无业务写入通过')
