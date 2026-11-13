import { test } from "node:test";
import assert from "node:assert/strict";
import { filterSettingsNavigation, settingsNavigation } from "../src/lib/settings-sections.js";
test("modal settings include local account and about while retaining all current configuration sections",()=>{
  assert.deepEqual(settingsNavigation.map(item=>item.id),["account","model","assignments","learning","system","about"]);
  assert.equal(new Set(settingsNavigation.map(item=>item.id)).size,settingsNavigation.length);
});
test("search finds configuration by its actual fields and unknown terms produce an empty result",()=>{
  for(const[query,id]of[["API Key","model"],["最终提交","assignments"],["播放倍速","learning"],["头像","account"],["检查更新","about"]]){
    const results=filterSettingsNavigation(query);
    assert.ok(results.some(section=>section.id===id),query);
  }
  assert.deepEqual(filterSettingsNavigation("nothing-matches"),[]);
  assert.equal(filterSettingsNavigation(" ").length,6);
});
