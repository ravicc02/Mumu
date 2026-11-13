import { test } from "node:test";
import assert from "node:assert/strict";
import { setImmediate } from "node:timers/promises";
import { collectAccountBatch } from "../src/lib/collection-batch.js";

test("one-click collection respects concurrency and a failed account does not stop the rest", async () => {
  const accounts = ["busy", "a", "b", "c"].map(id => ({ id, name:id }));
  const requests = {}, snapshots = [];
  let active = 0, maximum = 0;
  const completed = collectAccountBatch(accounts, { concurrency:2, changed:rows=>snapshots.push(rows), skip:account=>account.id==="busy"?"任务占用中":"",
    collect:async account=>{ active++; maximum=Math.max(maximum,active); try{return await new Promise((resolve,reject)=>requests[account.id]={resolve,reject});} finally{active--;} } });
  await setImmediate();
  assert.equal(active, 2);
  requests.a.reject(new Error("账号授权失效")); await setImmediate();
  assert.ok(requests.c);
  requests.b.resolve({status:"success",submittable:3}); requests.c.resolve({status:"partial",submittable:1});
  const result=await completed;
  assert.equal(maximum,2);
  assert.deepEqual(result.map(item=>item.status),["skipped","error","success","partial"]);
  assert.equal(result[1].message,"账号授权失效");
  assert.equal(snapshots[0][1].status,"queued");
  assert.equal(result[2].count,3);
});

test("a repeated account is collected only once and an empty batch makes no requests", async () => {
  const calls=[];
  const collect=async account=>{calls.push(account.id);return{status:"success",submittable:0};};
  await collectAccountBatch([{id:"one",name:"One"},{id:"one",name:"One"}],{collect});
  assert.deepEqual(calls,["one"]);
  assert.deepEqual(await collectAccountBatch([],{collect}),[]);
});
