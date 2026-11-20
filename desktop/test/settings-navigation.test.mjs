import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { filterSettingsNavigation, settingsNavigation } from "../src/features/settings/lib/navigation.js";
test("modal settings include common controls and all current configuration sections",()=>{
  assert.deepEqual(settingsNavigation.map(item=>item.id),["account","model","assignments","learning","attendance","system","data","about"]);
  assert.equal(new Set(settingsNavigation.map(item=>item.id)).size,settingsNavigation.length);
});
test("settings tabs use uniform two-character labels and no duplicated group headings",()=>{
  assert.deepEqual(settingsNavigation.map(item=>item.label),["账户","模型","作业","网课","签到","系统","数据","关于"]);
  for(const item of settingsNavigation)assert.equal([...item.label].length,2,item.id);
  assert.ok(settingsNavigation.filter(item=>item.id==="account").every(item=>!item.group));
});
test("search keeps legacy section names after labels are shortened",()=>{
  for(const[query,id]of[["模型配置","model"],["作业配置","assignments"],["网课配置","learning"],["签到域名","attendance"],["系统设置","system"],["本地账户","account"]]){
    assert.ok(filterSettingsNavigation(query).some(section=>section.id===id),query);
  }
});
test("search finds configuration by its actual fields and unknown terms produce an empty result",()=>{
  for(const[query,id]of[["开机自启","system"],["API Key","model"],["最终提交","assignments"],["章节题目","learning"],["播放倍速","learning"],["签到域名","attendance"],["头像","account"],["备份","data"],["检查更新","about"],["许可证","about"]]){
    const results=filterSettingsNavigation(query);
    assert.ok(results.some(section=>section.id===id),query);
  }
  assert.deepEqual(filterSettingsNavigation("nothing-matches"),[]);
  assert.equal(filterSettingsNavigation(" ").length,8);
});
test("about settings keeps update and logs without a reinitialization action",async()=>{
  const source=await readFile(new URL("../src/features/settings/components/AboutSettings.vue",import.meta.url),"utf8");
  assert.match(source,/查看更新日志/);
  assert.match(source,/changelogOpen = true/);
  assert.doesNotMatch(source,/openLink\(appInfo.repository \+ '\/releases'\)/);
  assert.match(source,/MIT 许可证/);
  assert.doesNotMatch(source,/无担保/);
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
  const system=await readFile(new URL("../src/features/settings/components/SettingsWorkspace.vue",import.meta.url),"utf8");
  assert.match(system,/<CommonSettings v-if="current.id === 'system'" :auto-check="autoCheck" :set-auto-check="setAutoCheck" \/>/);
});
test("assignment settings no longer duplicate the page-level detection schedule",async()=>{
  const source=await readFile(new URL("../src/features/settings/components/SettingsWorkspace.vue",import.meta.url),"utf8");
  assert.doesNotMatch(source,/自动检测与执行/);
  assert.doesNotMatch(source,/头歌自动评测/);
  assert.doesNotMatch(source,/学习通章节题目自动提交/);
  assert.doesNotMatch(source,/学习通最终提交/);
});
