import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const root = new URL("../../", import.meta.url);
const readme = await readFile(new URL("README.md", root), "utf8");

test("README hero keeps the product icon, text name, introduction, language links, and demo", () => {
  const markers = [
    'src="assets/brand/mumu.png"',
    "<h1>Mumu</h1>",
    "刷作业",
    "刷课",
    "签到为一体",
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
});

test("README language entry pages and demo media exist", async () => {
  const files = [
    "README.en.md",
    "README.zh-TW.md",
    "README.ko.md",
    "README.ja.md",
    "README.es.md",
    "assets/brand/mumu.png",
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
  for (const logo of ["xuexitong.png", "educoder.png", "zhihuishu.ico", "icourse.ico", "icve.ico", "zjy.ico", "yuketang.ico"]) {
    await readFile(new URL(`assets/platforms/${logo}`, root));
  }
});

test("language README pages stay aligned with the public asset layout", async () => {
  for (const file of ["README.en.md", "README.zh-TW.md", "README.ko.md", "README.ja.md", "README.es.md"]) {
    const content = await readFile(new URL(file, root), "utf8");
    for (const marker of ["assets/brand/mumu.png", "assets/demo/demo.gif", "assets/platforms/xuexitong.png", "## Star History", "CONTRIBUTING.md"]) {
      assert.ok(content.includes(marker), `${file} missing ${marker}`);
    }
    assert.doesNotMatch(content, /docs\/|AGENTS\.md/);
  }
  assert.doesNotMatch(readme, /docs\/|AGENTS\.md/);
});
