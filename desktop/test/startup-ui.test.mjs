import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const read = path => readFile(new URL(path, import.meta.url), "utf8");

test("startup uses a Mumu-only loading screen before the workspace", async () => {
  const [app, launch] = await Promise.all([
    read("../src/app/App.vue"),
    read("../src/features/brand/components/LaunchExperience.vue"),
  ]);
  assert.match(app, /<LaunchExperience\s+v-if="!launchComplete"/);
  assert.match(app, /<div v-show="launchComplete" class="app-shell"/);
  assert.match(app, /<h2>总控中心/);
  assert.match(launch, /<WaterMascot mood="working"/);
  assert.match(launch, /role="progressbar"/);
  assert.doesNotMatch(launch, /BrandOrbit/);
});

test("first-run setup is full screen and describes browser automation setup", async () => {
  const [startup, model] = await Promise.all([
    read("../src/features/brand/components/StartupExperience.vue"),
    read("../src/features/settings/components/ModelConfiguration.vue"),
  ]);
  assert.match(startup, /准备浏览器环境/);
  assert.match(startup, /Playwright 与 Browser Use/);
  assert.doesNotMatch(startup, /BrandOrbit/);
  assert.doesNotMatch(startup, /startup-wordmark|startup-mini-mascot/);
  assert.match(startup, /width: 100%; height: 100dvh/);
  assert.match(model, /placeholder="例如：My API Provider"/);
  assert.match(model, /placeholder="https:\/\/api\.example\.com\/v1"/);
  assert.match(model, /placeholder="your-model-name"/);
  assert.match(model, /class="reasoning-options"/);
});

test("model settings expose providers, protocol and fallback ordering", async () => {
  const model = await read("../src/features/settings/components/ModelConfiguration.vue");
  // 多供应商：左侧列表可拖拽排序，首位为默认。
  assert.match(model, /class="provider-list"/);
  assert.match(model, /拖拽排序，首位为默认/);
  assert.match(model, /dropProvider\(index\)/);
  // 协议与 Responses 开关。
  assert.match(model, /OpenAI 格式/);
  assert.match(model, /Anthropic 格式/);
  assert.match(model, /使用 Responses API/);
  assert.match(model, /use_responses_api/);
  // 供应商内模型优先级与跨供应商回退说明。
  assert.match(model, /<h4>模型优先级<\/h4>/);
  assert.match(model, /dropModel\(index\)/);
  assert.match(model, /全部不可用再换下一个供应商/);
});

test("learning workspace uses a searchable account picker", async () => {
  const source = await read("../src/features/learning/components/LearningWorkspace.vue");
  assert.match(source, /<LearningAccountPicker/);
  assert.doesNotMatch(source, /<select :value="accountId" aria-label="网课账号"/);
});
