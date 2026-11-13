import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { api } from "../src/app/api.js";

test("six additional platform entries share the backend catalog and ship local logos", async () => {
  const catalog=JSON.parse(await readFile(new URL("../../backend/app/services/platforms/catalog.json",import.meta.url),"utf8"));
  assert.equal(catalog.length,7);
  for(const item of catalog) {
    assert.ok((await readFile(new URL("../public/platforms/"+item.logo,import.meta.url))).length>100);
    assert.equal(item.learning,item.name!=="educoder");
    assert.equal(item.assignments,["chaoxing","educoder"].includes(item.name));
  }
});

test("platform authorization and course import call only our local endpoints", async () => {
  const original=globalThis.fetch,calls=[];
  globalThis.fetch=async (url,options)=>{calls.push({url,body:JSON.parse(options.body||"{}")});return {ok:true,json:async()=>({id:"local"})};};
  try {
    await api.createAccount("School","yuketang","https://school.yuketang.cn/v2/web/index");
    await api.confirmAuthorization("local");
    await api.addLearningCourse({account_id:"local",name:"Course",url:"https://school.yuketang.cn/ai-workspace/lms-graph/1"});
    assert.deepEqual(calls[0].body,{name:"School",platform:"yuketang",entry_url:"https://school.yuketang.cn/v2/web/index"});
    assert.ok(calls.every(item=>item.url.startsWith("http://127.0.0.1:8877/")));
    assert.match(calls[1].url,/confirm-authorization$/);
    assert.ok(calls[2].url.endsWith("/learning/courses"));
  } finally {globalThis.fetch=original;}
});
