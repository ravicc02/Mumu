import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(import.meta.url);
const { chromium } = process.env.BRAND_NODE_MODULES ? require(path.join(process.env.BRAND_NODE_MODULES, 'playwright')) : await import('playwright');
const origin = process.env.BRAND_PREVIEW_URL || 'http://127.0.0.1:5174';
const browser = await chromium.launch({ executablePath: process.env.BRAND_CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true });
const page = await browser.newPage({ viewport: { width: 1024, height: 470 }, deviceScaleFactor: 1 });
const frameRoot = path.join(root, '.runtime/brand-frames'), assets = path.join(root, 'docs/assets');
await fs.mkdir(frameRoot, { recursive: true }); await fs.mkdir(path.join(root, 'desktop/public/brand'), { recursive: true });
try {
  await page.goto(origin);
  const style = (await fs.readFile(path.join(root, 'desktop/src/mascot.css'), 'utf8')).match(/:root\s*\{[^}]+\}/)[0];
  await page.setContent('<style>' + style + 'html,body{margin:0;background:transparent}canvas{width:512px;height:512px;display:block}</style><canvas></canvas>');
  const icons = await page.evaluate(async () => {
    const { createWaterMascot } = await import('/src/lib/water-mascot.js');
    const canvas = document.querySelector('canvas'), mascot = createWaterMascot(canvas, { detail: true });
    mascot.setState({ mood: 'working', water: .55, total: 0, paused: true });
    const images = [16, 24, 32, 48, 64, 128, 256, 512].map(size => {
      const output = document.createElement('canvas'); output.width = output.height = size;
      output.getContext('2d').drawImage(canvas, 0, 0, size, size);
      return { size, png: output.toDataURL('image/png').split(',')[1] };
    }); mascot.dispose(); return images;
  });
  const png = Buffer.from(icons.at(-1).png, 'base64');
  for (const file of ['desktop/public/brand/mumu.png', 'desktop/build/icon.png', 'docs/assets/mumu.png']) await fs.writeFile(path.join(root, file), png);
  const entries = icons.slice(0, -1), header = Buffer.alloc(6 + entries.length * 16), buffers = [];
  header.writeUInt16LE(1, 2); header.writeUInt16LE(entries.length, 4);
  let offset = header.length;
  entries.forEach((item, index) => {
    const data = Buffer.from(item.png, 'base64'), at = 6 + index * 16;
    header[at] = header[at + 1] = item.size === 256 ? 0 : item.size;
    header.writeUInt16LE(1, at + 4); header.writeUInt16LE(32, at + 6);
    header.writeUInt32LE(data.length, at + 8); header.writeUInt32LE(offset, at + 12);
    buffers.push(data); offset += data.length;
  });
  await fs.writeFile(path.join(root, 'desktop/build/icon.ico'), Buffer.concat([header, ...buffers]));
  await page.setContent('<style>html,body{margin:0;background:#f4f7f8;color:#203c49;font-family:"Microsoft YaHei",sans-serif}.frame{height:470px;text-align:center}canvas{display:block;width:1024px;height:380px}h1{margin:-5px 0 0;font-size:38px;font-weight:600;letter-spacing:0}.english{margin-top:8px;font-size:12px;color:#66808c}</style><div class="frame"><canvas></canvas><h1>沐沐</h1><div class="english">MUMU</div></div>');
  await page.evaluate(async () => { const { createBrandScene } = await import('/src/lib/brand-scene.js'); window.brandScene = createBrandScene(document.querySelector('canvas'), { animate: false }); });
  for (let frame = 0; frame < 96; frame++) {
    await page.evaluate(seconds => window.brandScene.renderAt(seconds), frame / 12);
    await page.screenshot({ path: path.join(frameRoot, String(frame).padStart(3, '0') + '.png') });
  }
  await fs.copyFile(path.join(frameRoot, '012.png'), path.join(assets, 'mumu-bots.png'));
  for (const [file, pngFile, width, height] of [['logo.svg', 'mumu.png', 512, 512], ['banner.svg', 'mumu-bots.png', 1024, 470]]) {
    const content = await fs.readFile(path.join(assets, pngFile));
    await fs.writeFile(path.join(assets, file), '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 ' + width + ' ' + height + '"><title>沐沐</title><image width="' + width + '" height="' + height + '" xlink:href="data:image/png;base64,' + content.toString('base64') + '"/></svg>\n');
  }
} finally { await browser.close(); }
await new Promise((resolve, reject) => {
  const child = spawn(process.env.BRAND_FFMPEG || 'ffmpeg', ['-hide_banner', '-loglevel', 'error', '-y', '-framerate', '12', '-i', path.join(frameRoot, '%03d.png'), '-filter_complex', '[0:v]split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4', '-loop', '0', path.join(assets, 'mumu-bots.gif')], { windowsHide: true, stdio: 'inherit' });
  child.on('error', reject); child.on('exit', code => code === 0 ? resolve() : reject(new Error('FFmpeg exited with ' + code)));
});
console.log('Rendered MuMu PNG, Windows ICO, static brand plates and 8-second looping GIF.');
