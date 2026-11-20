import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { allowedProjectLink, appInfo, displayVersion, checkPublishedRelease, loadUpdatePreference, saveUpdatePreference } from "../src/app/lib/product.js";
const require=createRequire(import.meta.url),nativeLinks=require("../electron/external-links.cjs");
const release=version=>({tag_name:version,html_url:appInfo.repository+"/releases/tag/"+version,draft:false,prerelease:false});
const response=(status,value)=>({status,ok:status===200,json:async()=>value});
test("about version matches the packaged application manifest",()=>assert.equal(appInfo.version,require("../package.json").version));
test("product identity uses the Mumu repository and release API",()=>{
  assert.equal(appInfo.name,"Mumu");
  assert.equal(appInfo.repository,"https://github.com/ravicc02/Mumu");
  assert.equal(appInfo.releaseApi,"https://api.github.com/repos/ravicc02/Mumu/releases/latest");
});
test("about displays the base version without changing the update version",()=>{
  assert.equal(displayVersion, require("../package.json").version.split(/[+-]/)[0]);
  assert.match(displayVersion, /^\d+\.\d+\.\d+$/);
});
test("a stable release supersedes a local prerelease of the same version",async()=>{
  const result=await checkPublishedRelease({currentVersion:"0.5.0-rc.1",fetcher:async()=>response(200,release("v0.5.0"))});
  assert.equal(result.status,"available");
});
test("update check distinguishes newer, same, and older published versions",async()=>{
  for(const [remote,expected]of[["v0.4.3","available"],["v0.4.2","current"],["v0.4.1","current"]]){
    const result=await checkPublishedRelease({currentVersion:"0.4.2",fetcher:async()=>response(200,release(remote))});
    assert.equal(result.status,expected);assert.equal(result.version,remote);
  }
});
test("missing releases or failed requests never claim an up-to-date version",async()=>{
  assert.equal((await checkPublishedRelease({fetcher:async()=>response(404)})).status,"unpublished");
  await assert.rejects(checkPublishedRelease({fetcher:async()=>response(403)}),/拒绝访问/);
  await assert.rejects(checkPublishedRelease({fetcher:async()=>response(503)}),/暂时无法/);
  await assert.rejects(checkPublishedRelease({fetcher:async()=>{throw new Error("network");}}),/network/);
});
test("update checks omit credentials and only accept verified project release links",async()=>{
  await checkPublishedRelease({fetcher:async(url,options)=>{assert.equal(url,appInfo.releaseApi);assert.equal(options.credentials,"omit");assert.equal(options.headers.Authorization,undefined);return response(404);}});
  for(const value of [{...release("v0.4.3"),html_url:"https://untrusted.example/download"},{...release("v0.4.3"),html_url:appInfo.legacyRepository+"/releases/tag/v0.4.3"},{...release("v0.4.3"),prerelease:true},release("unknown-version")])await assert.rejects(checkPublishedRelease({fetcher:async()=>response(200,value)}),/无法核验/);
});
test("update rate limits distinguish denied access and explain retry time",async()=>{
  for (const status of [403,429]) {
    const limited={...response(status),headers:{get:name=>name==="x-ratelimit-remaining"?"0":name==="retry-after"?"60":null}};
    await assert.rejects(checkPublishedRelease({fetcher:async()=>limited}),/频率受限.*60 秒/);
  }
});
test("repeated update checks reuse successful responses without sharing injected fetchers",async()=>{
  let calls=0;
  const fetcher=async()=>{calls++;return response(200,release("v0.4.3"));};
  await checkPublishedRelease({currentVersion:"0.4.2",fetcher});
  await checkPublishedRelease({currentVersion:"0.4.2",fetcher});
  assert.equal(calls,1);
});
test("browser and native project links share the same restricted boundary",()=>{
  for(const url of [appInfo.repository,appInfo.repository+"/releases",appInfo.repository+"/releases/tag/v0.6.0",appInfo.legacyRepository,appInfo.legacyRepository+"/releases/tag/v0.5.1"]){assert.equal(allowedProjectLink(url),true);assert.equal(nativeLinks.allowedProjectLink(url),true);}
  for(const url of ["file:///C:/Windows/notepad.exe","javascript:alert(1)","https://github.com.evil.example/ravicc02/Mumu","https://github.com/other/repository",appInfo.repository+"-other",appInfo.repository+"?secret=private"]){assert.equal(allowedProjectLink(url),false);assert.equal(nativeLinks.allowedProjectLink(url),false);}
});
test("license links are allowed precisely in browser and desktop",()=>{
  for (const root of [appInfo.repository,appInfo.legacyRepository]) {
    for (const suffix of ["/blob/main/LICENSE","/blob/main/LICENSE#license"]) {
      assert.equal(allowedProjectLink(root+suffix),true);
      assert.equal(nativeLinks.allowedProjectLink(root+suffix),true);
    }
    for (const suffix of ["/blob/main/LICENSE-other","/blob/other/LICENSE","/blob/main/secret","/blob/main/LICENSE?raw=1"]) {
      assert.equal(allowedProjectLink(root+suffix),false);
      assert.equal(nativeLinks.allowedProjectLink(root+suffix),false);
    }
  }
});
test("rate-limit cooldown prevents repeated requests across versions and expires",async()=>{
  let time=1_800_000_000_000,calls=0;
  const fetcher=async()=>{calls++;return calls===1?{...response(429),headers:{get:name=>name==="retry-after"?"60":null}}:response(200,release("v0.10.7"));};
  const now=()=>time;
  await assert.rejects(checkPublishedRelease({fetcher,now}),/60 秒/);
  time+=10000;
  await assert.rejects(checkPublishedRelease({fetcher,now,currentVersion:"0.9.0"}),/50 秒/);
  assert.equal(calls,1);
  time+=50001;
  assert.equal((await checkPublishedRelease({fetcher,now})).status,"current");
  assert.equal(calls,2);
});
test("rate-limit retry date, reset and missing headers all establish cooldown",async()=>{
  for (const headers of [
    {"retry-after":new Date(1_800_000_060_000).toUTCString()},
    {"x-ratelimit-remaining":"0","x-ratelimit-reset":"1800000060"},
    {},
  ]) {
    let calls=0;
    const fetcher=async()=>{calls++;return {...response(429),headers:{get:name=>headers[name]??null}};};
    for(let i=0;i<2;i++) await assert.rejects(checkPublishedRelease({fetcher,now:()=>1_800_000_000_000}),/60 秒/);
    assert.equal(calls,1);
  }
});
test("automatic checks are opt-in and survive a local reload",()=>{
  let value=null;const store={getItem:()=>value,setItem:(_key,next)=>value=next};
  assert.equal(loadUpdatePreference(store),false);saveUpdatePreference(true,store);assert.equal(loadUpdatePreference(store),true);
  value="invalid";assert.equal(loadUpdatePreference(store),false);
});
