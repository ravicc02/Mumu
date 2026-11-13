import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { auditSource } from "../audit-source.mjs";

test("source audit rejects credentials without printing their content", t => {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),"studio-audit-"));
  t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
  const key="sk-"+"a".repeat(40);
  fs.writeFileSync(path.join(root,"config.js"),"const key = "+JSON.stringify(key));
  const result=auditSource(root,["config.js"]);
  assert.equal(result[0].rule,"provider-key");
  assert.equal(result[0].line,1);
  assert.ok(!JSON.stringify(result).includes(key));
});

test("source audit permits examples and rejects local data and escaping paths", t => {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),"studio-audit-"));
  t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
  for(const file of [".env",".env.example","main.log","storage-state.json"]) fs.writeFileSync(path.join(root,file),"example");
  assert.deepEqual(auditSource(root,[".env.example"]),[]);
  assert.equal(auditSource(root,[".env","main.log","storage-state.json","../outside"] ).length,4);
});
