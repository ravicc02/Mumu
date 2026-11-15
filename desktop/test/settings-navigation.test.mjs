import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { filterSettingsNavigation, settingsNavigation } from "../src/features/settings/lib/navigation.js";
test("modal settings include common controls and all current configuration sections",()=>{
  assert.deepEqual(settingsNavigation.map(item=>item.id),["common","account","model","assignments","learning","attendance","system","about"]);
  assert.equal(new Set(settingsNavigation.map(item=>item.id)).size,settingsNavigation.length);
});
test("search finds configuration by its actual fields and unknown terms produce an empty result",()=>{
  for(const[query,id]of[["开机自启","common"],["API Key","model"],["最终提交","assignments"],["章节题目","learning"],["播放倍速","learning"],["签到域名","attendance"],["头像","account"],["检查更新","about"],["许可证","about"]]){
    const results=filterSettingsNavigation(query);
    assert.ok(results.some(section=>section.id===id),query);
  }
  assert.deepEqual(filterSettingsNavigation("nothing-matches"),[]);
  assert.equal(filterSettingsNavigation(" ").length,8);
});
test("about settings keeps update and logs without a reinitialization action",async()=>{
  const source=await readFile(new URL("../src/features/settings/components/AboutSettings.vue",import.meta.url),"utf8");
  assert.match(source,/更新记录/);
  assert.match(source,/GPL-3\.0-only/);
  assert.match(source,/无担保/);
  assert.match(source,/日志目录/);
  assert.match(source,/检查更新/);
  assert.doesNotMatch(source,/重新初始化/);
});
test("common settings expose startup launch and update controls",async()=>{
  const source=await readFile(new URL("../src/features/settings/components/CommonSettings.vue",import.meta.url),"utf8");
  assert.match(source,/开机自启/);
  assert.match(source,/启动时自动检查更新/);
  assert.match(source,/getAutoLaunch/);
  assert.match(source,/安装版可用/);
});
test("assignment settings no longer duplicate the page-level detection schedule",async()=>{
  const source=await readFile(new URL("../src/features/settings/components/SettingsWorkspace.vue",import.meta.url),"utf8");
  assert.doesNotMatch(source,/自动检测与执行/);
  assert.doesNotMatch(source,/头歌自动评测/);
  assert.doesNotMatch(source,/学习通章节题目自动提交/);
  assert.doesNotMatch(source,/学习通最终提交/);
});
