"""模型用量界面的隔离浏览器验收，不访问真实模型服务。"""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=True)
    page = browser.new_page(viewport={"width": 1182, "height": 916})
    errors, requests = [], []
    mode = {"value": "normal"}
    page.on("pageerror", lambda e: errors.append(str(e)))
    config = {"providers": [{"id": "demo", "name": "演示供应商", "base_url": "https://example.test/v1", "api_key_configured": True, "models": ["demo-model"], "api_format": "openai"}]}
    def api(route):
        from urllib.parse import urlparse, parse_qs
        requests.append(route.request.url)
        if "model-usage" not in route.request.url:
            route.fulfill(content_type="application/json", body=json.dumps(config)); return
        if mode["value"] == "error":
            route.fulfill(status=500, content_type="application/json", body='{"detail":"读取失败，请重试"}'); return
        if '/pricing' in route.request.url:
            if route.request.method == 'POST':
                rule = json.loads(route.request.post_data)
                mode['rule'] = rule | {'id': 'price-demo', 'effective_at': '2026-11-20T03:00:00+08:00'}
                route.fulfill(json=mode['rule']); return
            route.fulfill(json={'rules': [mode['rule']] if 'rule' in mode else []}); return
        if '/tools' in route.request.url:
            route.fulfill(json={'total': 1, 'tools': ['tasks.list'], 'groups': [{'tool': 'tasks.list', 'requests': 1, 'succeeded': 1, 'errors': 0, 'running': 0, 'average_latency_ms': 1000}], 'records': [{'id': 'tool-1', 'tool': 'tasks.list', 'created_at': '2026-11-20T02:50:00+08:00', 'status': 'succeeded', 'latency_ms': 1000}]}); return
        query = parse_qs(urlparse(route.request.url).query)
        empty = mode["value"] == "empty"
        count = 0 if empty else 62
        offset = int(query.get("offset", [0])[0])
        record = {"id": "demo", "created_at": "2026-11-20T02:50:00+08:00", "provider_name": "演示供应商", "model": "demo-model-with-a-very-long-name-that-must-not-break-layout", "total_tokens": 102400, "input_tokens": 100000, "output_tokens": 2400, "cached_tokens": 80000, "latency_ms": 3300, "status": "succeeded", "http_status": None}
        records = [] if empty else [dict(record, id=str(i)) for i in range(offset, min(offset+50, count))]
        summary = dict(requests=count, succeeded=count, errors=0, usage_requests=count, input_tokens=6200000, output_tokens=148800, total_tokens=6348800, cached_tokens=4960000, cache_creation_tokens=0, cache_hit_rate=.8, average_latency_ms=3300)
        summary.update(costs={'CNY': 1.2345}, priced_requests=count, cache_requests=count, cache_request_hit_rate=1)
        group = summary | {'provider_id': 'demo', 'provider_name': '演示供应商', 'model': 'demo-model'}
        route.fulfill(content_type="application/json", body=json.dumps(dict(summary=summary, total=count, records=records, models=["demo-model"], providers=[{'id': 'demo', 'name': '演示供应商'}], by_provider=[group], by_model=[group], offset=offset, limit=50)))
    page.route("**/api/**", api)
    page.route("http://127.0.0.1:5174/model-usage-test", lambda route: route.fulfill(content_type="text/html", body='<html><body style="margin:0;background:#f7f9fa"><div id="test" style="padding:32px;max-width:1200px;margin:auto"></div></body></html>'))
    page.goto("http://127.0.0.1:5174/model-usage-test")
    page.evaluate('''async () => {
      await import('/src/styles/settings.css');
      await import('/src/styles/workbench.css');
      const {createApp,h} = await import('/node_modules/.vite/deps/vue.js');
      const {default:Model} = await import('/src/features/settings/components/ModelConfiguration.vue');
      createApp({render:()=>h(Model)}).mount('#test');
    }''')
    page.get_by_placeholder("例如：My API Provider").fill("未保存草稿")
    page.get_by_role("tab", name="统计", exact=True).click()
    page.get_by_text("共 62 条记录").wait_for()
    assert page.get_by_role("tablist", name="统计分类").get_by_role("tab").count() == 5
    page.get_by_label('筛选供应商', exact=True).select_option('demo')
    page.get_by_text('共 62 条记录').wait_for()
    assert 'provider_id=demo' in requests[-1]
    page.get_by_label('详细记录', exact=True).check()
    page.locator('.usage-detail-row').first.wait_for()
    page.locator('.usage-detail-row summary').first.click()
    assert page.get_by_text('仅显示安全元数据，不记录请求正文、响应正文或密钥。').count() == 50
    page.get_by_role("button", name="近 7 天").click()
    page.get_by_text("共 62 条记录").wait_for()
    assert "period=7d" in requests[-1]
    page.get_by_label("筛选模型", exact=True).select_option("demo-model")
    page.get_by_text("共 62 条记录").wait_for()
    page.get_by_label("筛选请求状态").select_option("succeeded")
    page.get_by_text("共 62 条记录").wait_for()
    page.get_by_role("button", name="下一页").click()
    page.get_by_text("51–62 / 62").wait_for()
    assert "offset=50" in requests[-1]
    page.get_by_role("button", name="上一页").click()
    page.get_by_text("1–50 / 62").wait_for()
    for width in (1182, 1222, 1440):
        page.set_viewport_size({"width": width, "height": 916})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    screenshot = ROOT / "docs/assets/model-usage-test.png"
    page.screenshot(path=str(screenshot))
    page.get_by_role("tab", name="模型", exact=True).click()
    assert page.get_by_placeholder("例如：My API Provider").input_value() == "未保存草稿"
    page.get_by_role("tab", name="统计", exact=True).click()
    page.get_by_text("共 62 条记录").wait_for()
    page.get_by_role('tab', name='供应商统计', exact=True).click()
    page.locator('.usage-table tbody tr').first.wait_for()
    assert page.locator('.usage-table').inner_text().find('演示供应商') >= 0
    page.get_by_role('tab', name='模型统计', exact=True).click()
    page.locator('.usage-table tbody tr').first.wait_for()
    page.get_by_role('tab', name='工具统计', exact=True).click()
    page.get_by_text('共 1 条执行记录').wait_for()
    page.get_by_label('筛选工具', exact=True).select_option('tasks.list')
    page.get_by_text('共 1 条执行记录').wait_for()
    page.get_by_role('tab', name='定价配置', exact=True).click()
    page.get_by_text('尚未配置单价。', exact=False).wait_for()
    page.get_by_label('供应商', exact=True).select_option('demo')
    page.get_by_label('完整模型名称', exact=True).fill('demo-model')
    for label in ['普通输入', '输出', '缓存读取', '缓存创建']:
        page.get_by_label(label, exact=True).fill('1')
    page.get_by_role('button', name='保存新单价').click()
    page.get_by_text('单价已保存，从现在起生效；历史请求不重新计价。').wait_for()
    assert mode['rule']['input_price'] == 1
    assert page.locator('.usage-table tbody tr').count() == 1
    for width in (1182, 1222, 1440):
        page.set_viewport_size({'width': width, 'height': 916})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.get_by_role('tab', name='请求日志', exact=True).click()
    page.get_by_text('共 62 条记录').wait_for()
    mode["value"] = "error"
    page.get_by_role("button", name="刷新统计").click()
    page.get_by_role("alert").wait_for()
    mode["value"] = "empty"
    page.get_by_role("button", name="重试", exact=True).click()
    page.get_by_text("该时间范围内暂无模型调用").wait_for()
    assert not errors, errors
    assert all("model-config" in url or "model-usage" in url for url in requests)
    browser.close()
    print("通过：两个子 Tab、草稿保留、时间/模型/状态筛选、分页、错误重试、空态、双尺寸无溢出、控制台检查；截图：" + str(screenshot))
