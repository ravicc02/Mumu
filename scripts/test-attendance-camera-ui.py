"""用模拟摄像头验证分享页视频和识别循环，不执行真实签到。"""
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
with sync_playwright() as p:
    browser = p.chromium.launch(channel='chrome', headless=True, args=['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'])
    context = browser.new_context(permissions=['camera'], viewport={'width':390,'height':844}, is_mobile=True)
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.route('http://127.0.0.1:5174/camera-test', lambda r: r.fulfill(content_type='text/html', body=(ROOT/'backend/app/services/attendance/share.html').read_text(encoding='utf-8')))
    page.route('**/assets/qr-decoder.js', lambda r: r.fulfill(content_type='application/javascript', body='window.framesDecoded=[]; window.decodeQrCode=(data,width,height)=>{framesDecoded.push({width,height});return null;}'))
    page.goto('http://127.0.0.1:5174/camera-test')
    page.evaluate('openScanner()')
    page.wait_for_function('document.getElementById("video").videoWidth > 0 && framesDecoded.length > 2')
    metrics = page.evaluate('''() => ({settings:stream.getVideoTracks()[0].getSettings(), width:el('video').videoWidth,height:el('video').videoHeight, decoded:framesDecoded, region:scanRegion(el('video').videoWidth,el('video').videoHeight)})''')
    assert metrics['width'] >= 1280, metrics
    assert any(max(f['width'],f['height'])<=1280 for f in metrics['decoded'])
    page.wait_for_function('framesDecoded.some(f=>Math.max(f.width,f.height)===1600)')
    page.evaluate('window.oldTrack=stream.getVideoTracks()[0];closeScanner()')
    assert page.evaluate('oldTrack.readyState') == 'ended'
    assert page.evaluate('el("video").srcObject === null')
    assert not errors, errors
    browser.close()
    print('模拟摄像头：高清取流、扫码框裁剪、全画面兜底和关闭释放通过；未执行签到')
