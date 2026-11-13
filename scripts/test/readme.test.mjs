import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const root = new URL("../../", import.meta.url);
const readme = await readFile(new URL("README.md", root), "utf8");

test("README hero keeps the product icon, text name, introduction, language links, and demo", () => {
  const markers = [
    'src="assets/brand/mumu-loop.gif"',
    "<h1>Mumu</h1>",
    "辅助大学生校园生活以及学习的一体式客户端",
    "README.en.md",
    "README.zh-TW.md",
    "README.ko.md",
    "README.ja.md",
    "README.es.md",
    'src="assets/demo/demo.gif"'
  ];
  const positions = markers.map(marker => readme.indexOf(marker));
  for (let index = 0; index < markers.length; index += 1) {
    assert.notEqual(positions[index], -1, `missing README marker: ${markers[index]}`);
    if (index > 0) assert.ok(positions[index] > positions[index - 1], `README marker is out of order: ${markers[index]}`);
  }
  assert.ok(readme.includes("assets/demo/demo.mp4"), "missing full demo video link");
  assert.ok(!readme.includes("<hr>"), "README hero should rely on the GitHub heading rule instead of an extra divider");
});

test("README language entry pages and demo media exist", async () => {
  const files = [
    "README.en.md",
    "README.zh-TW.md",
    "README.ko.md",
    "README.ja.md",
    "README.es.md",
    "assets/brand/mumu.png",
    "assets/brand/mumu-loop.gif",
    "assets/demo/demo.gif",
    "assets/demo/demo.mp4"
  ];
  for (const file of files) await readFile(new URL(file, root));
});

test("README body contains safety notices, features, platform logos, and star history", async () => {
  const markers = [
    "## 使用须知",
    "> [!CAUTION]",
    "> [!IMPORTANT]",
    "### 免责声明",
    "> 本项目仅用于技术研究与学习交流",
    "## 功能介绍",
    "## 支持的平台",
    'src="assets/platforms/xuexitong.png"',
    'src="assets/platforms/educoder.png"',
    'src="assets/platforms/zhihuishu.ico"',
    'src="assets/platforms/icourse.ico"',
    'src="assets/platforms/icve.ico"',
    'src="assets/platforms/zjy.ico"',
    'src="assets/platforms/yuketang.ico"',
    "## Star History",
    "https://api.star-history.com/svg?repos=ravicc02/mumu"
  ];
  const positions = markers.map(marker => readme.indexOf(marker));
  for (let index = 0; index < markers.length; index += 1) {
    assert.notEqual(positions[index], -1, `missing README body marker: ${markers[index]}`);
    if (index > 0) assert.ok(positions[index] > positions[index - 1], `README body marker is out of order: ${markers[index]}`);
  }
  assert.ok(!readme.includes("当前能力"), "platform table should not include the current capability column");
  assert.equal(readme.lastIndexOf("\n## "), readme.indexOf("\n## Star History"), "Star History should be the final section");
  for (const logo of ["xuexitong.png", "educoder.png", "zhihuishu.ico", "icourse.ico", "icve.ico", "zjy.ico", "yuketang.ico"]) {
    await readFile(new URL(`assets/platforms/${logo}`, root));
  }
});

test("language README pages stay aligned with the public asset layout", async () => {
  const disclaimerHeadings = {
    "README.en.md": "### Disclaimer",
    "README.zh-TW.md": "### 免責聲明",
    "README.ko.md": "### 면책 조항",
    "README.ja.md": "### 免責事項",
    "README.es.md": "### Descargo de responsabilidad"
  };
  for (const [file, disclaimer] of Object.entries(disclaimerHeadings)) {
    const content = await readFile(new URL(file, root), "utf8");
    for (const marker of ["assets/brand/mumu-loop.gif", "assets/demo/demo.gif", "assets/platforms/xuexitong.png", disclaimer, "## Star History", "CONTRIBUTING.md"]) {
      assert.ok(content.includes(marker), `${file} missing ${marker}`);
    }
    assert.ok(!content.includes("<hr>"), `${file} should rely on the GitHub heading rule instead of an extra divider`);
    assert.ok(!content.includes("assets/brand/mumu.png"), `${file} should use the animated Mumu asset`);
    assert.equal(content.lastIndexOf("\n## "), content.indexOf("\n## Star History"), `${file} should keep Star History as the final section`);
    assert.doesNotMatch(content, /docs\/|AGENTS\.md/);
  }
  assert.doesNotMatch(readme, /docs\/|AGENTS\.md/);
});
