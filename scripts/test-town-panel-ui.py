"""使用隔离任务验证任务面板四列与图标布局。"""
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=True)
    page = browser.new_page(viewport={"width": 1222, "height": 916})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("http://127.0.0.1:5174/town-panel-test", lambda route: route.fulfill(content_type="text/html", body='<div id="test"></div>'))
    page.goto("http://127.0.0.1:5174/town-panel-test")
    page.evaluate("""async () => {
      await import('/src/styles/workbench.css');
      const {createApp,h} = await import('/node_modules/.vite/deps/vue.js');
      const {default: Town} = await import('/src/features/task-town/components/TaskTown.vue');
      const names=['头歌作业6-黑盒软件测试方法','新建作业20261116132151'];
      const tasks=[...Array.from({length:14},(_,i)=>({id:'done'+i,account_id:'a',assignment_id:'done'+i,assignment_title:names[i%2],account_name:'演示账号',platform:'educoder',status:'completed'})),
        ...Array.from({length:9},(_,i)=>({id:'error'+i,account_id:'a',assignment_id:'error'+i,assignment_title:names[i%2],account_name:'演示账号',platform:'educoder',status:'interrupted'}))];
      createApp({render:()=>h('main',{class:'dashboard-main town-dashboard'},[h('section',{class:'dashboard-heading'},'总控中心'),h(Town,{ready:true,online:true,tasks})])}).mount('#test');
    }""")
    groups = page.locator(".town-panel-group")
    groups.first.wait_for()
    assert groups.count() == 6
    for width in (1182, 1222, 1440):
        page.set_viewport_size({"width": width, "height": 916})
        boxes = [groups.nth(i).bounding_box() for i in range(6)]
        assert len({round(boxes[i]["x"]) for i in range(4)}) == 4, (width, boxes)
        assert round(boxes[4]["x"]) == round(boxes[0]["x"]), (width, boxes)
        assert boxes[4]["y"] > boxes[2]["y"]
        assert boxes[4]["y"] < 900, (width, boxes)
        completed_list = groups.nth(2).locator(".town-panel-list")
        assert completed_list.evaluate("element => element.scrollHeight > element.clientHeight"), width
        completed_list.evaluate("element => element.scrollTop = element.scrollHeight")
        assert groups.nth(2).get_by_role("heading", name="已完成").is_visible()
        task = page.locator(".town-panel-task").first
        row = task.bounding_box()
        logo = task.locator(".platform-logo").bounding_box()
        arrow = task.locator("svg").last.bounding_box()
        assert logo["width"] == 24 and logo["height"] == 24, (width, logo)
        assert arrow["width"] >= 14 and row["x"] <= logo["x"] < arrow["x"] < row["x"] + row["width"], (width, row, logo, arrow)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
    assert not errors, errors
    browser.close()
    print("四列布局、图标尺寸及双行状态分组检查通过")
