"""使用隔离页面验证复制兼容通道、许可证操作和限流冷却，无业务写入。"""
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(channel='chrome', headless=True)
    page = browser.new_page(viewport={'width': 1060, 'height': 850})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.route('**/clipboard-update-test', lambda route: route.fulfill(
        content_type='text/html', body='<input id="focus" value="选择测试"><button id="copy">复制</button><div id="test"></div>'))
    page.goto('http://127.0.0.1:5174/clipboard-update-test')
    page.evaluate('''async () => {
      const {copyText} = await import('/src/app/lib/clipboard.js');
      const {allowedProjectLink, appInfo, checkPublishedRelease} = await import('/src/app/lib/product.js');
      const {createApp,h} = await import('/node_modules/.vite/deps/vue.js');
      const {default:About} = await import('/src/features/settings/components/AboutSettings.vue');
      window.copied = false; window.opened = '';
      document.querySelector('#copy').onclick = async () => {
        const input = document.querySelector('#focus'); input.focus(); input.setSelectionRange(1,3);
        try { await copyText('https://example.test/扫码', {desktop:{},navigator:{}}); }
        catch(error) {window.copyError = error.message; return;}
        if(document.activeElement !== input || input.selectionStart !== 1 || input.selectionEnd !== 3) {window.copyError = `复制破坏焦点或选区: ${document.activeElement?.id},${input.selectionStart},${input.selectionEnd}`; return;}
        window.copied = true;
      };
      createApp({render:()=>h(About,{visible:true,update:{status:'error',message:'模拟限流'},setAutoCheck:()=>{},checkUpdates:()=>{},openExternal:async url=>{
        if(!allowedProjectLink(url)) throw Error('许可证链接被拦截');
        window.opened = url;
      },openLogs:()=>{},close:()=>{}})}).mount('#test');
      let calls=0, time=1800000000000;
      const fetcher=async()=>{calls++;return {status:429,headers:{get:name=>name==='retry-after'?'1175':null}};};
      for(let i=0;i<3;i++) {
        try {await checkPublishedRelease({fetcher,now:()=>time});throw Error('限流未报告');}
        catch(error) {if(!error.message.includes('频率受限')) throw error;}
        time+=1000;
      }
      if(calls!==1) throw Error('冷却期间重复请求');
    }''')
    page.locator('#copy').click()
    page.wait_for_function('window.copied === true || window.copyError')
    assert not page.evaluate('window.copyError'), page.evaluate('window.copyError')
    page.get_by_role('button', name='MIT', exact=True).click()
    page.wait_for_function("window.opened.endsWith('/blob/main/LICENSE')")
    assert page.locator('.about-error').count() == 0
    assert not errors, errors
    browser.close()
    print('PASS: 真实浏览器兼容复制成功且焦点选区恢复；关于页许可证按钮通过白名单；1175 秒冷却内三次检查仅一次请求；无页面异常')
