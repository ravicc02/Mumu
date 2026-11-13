import { test } from "node:test";
import assert from "node:assert/strict";
import { api } from "../src/api.js";
import { assignmentReadiness } from "../src/lib/assignment-readiness.js";
import { buildWorkflowStages } from "../src/lib/job-state.js";
import { overviewResult } from "../src/lib/task-overview.js";

test("EduCoder course discovery and selection use local, account-scoped APIs",async()=>{
  const original=globalThis.fetch,calls=[];
  globalThis.fetch=async(url,options={})=>{calls.push({url,options});return{ok:true,json:async()=>[]};};
  try {
    await api.platformCourses("account-one");
    await api.collectPlatformCourses("account-one");
    await api.selectPlatformCourses("account-one",["101","202"]);
    assert.equal(calls[0].url,"http://127.0.0.1:8877/api/accounts/account-one/courses");
    assert.equal(calls[1].options.method,"POST");
    assert.equal(calls[2].options.method,"PUT");
    assert.deepEqual(JSON.parse(calls[2].options.body),{course_ids:["101","202"]});
  } finally {globalThis.fetch=original;}
});

test("saved code does not impersonate passed challenge progress",()=>{
  const job={platform:"educoder",status:"needs_intervention",submission_status:"filled_pending_review",total_units:4,phase:"review"};
  const stages=buildWorkflowStages(job,[{kind:"quiz_extracted"},{kind:"draft_saved"}],{completedCount:0});
  const execution=stages.find(item=>item.id==="execute");
  assert.equal(execution.state,"attention");
  assert.equal(execution.detail,"已通过 0/4 关");
  assert.equal(overviewResult({...job,task_kind:"assignment"},()=>{throw new Error("wrong result label");}),"代码已保存，待审核评测");
});

test("experiment list reports platform challenge counts instead of filled answers",()=>{
  const state=assignmentReadiness({assessment:{runtime:"educoder",filled_units:2,unit_count:4,answer_state:"partial"}});
  assert.equal(state.label,"2/4 关已通过");
  assert.ok(!state.label.includes("填写"));
});

test("account avatars use our local cached image rather than authenticated upstream links",()=>{
  const url=api.accountAvatarUrl({id:"account-one",identity_verified_at:"2026-11-12T12:00:00+08:00",avatar_url:"https://untrusted.example/private"});
  assert.ok(url.startsWith("http://127.0.0.1:8877/api/accounts/account-one/avatar?v="));
  assert.ok(!url.includes("untrusted.example"));
});
