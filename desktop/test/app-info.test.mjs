import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { allowedProjectLink, appInfo, checkPublishedRelease, loadUpdatePreference, saveUpdatePreference } from "../src/lib/app-info.js";
const require=createRequire(import.meta.url),nativeLinks=require("../electron/external-links.cjs");
const release=version=>({tag_name:version,html_url:appInfo.repository+"/releases/tag/"+version,draft:false,prerelease:false});
const response=(status,value)=>({status,ok:status===200,json:async()=>value});
test("about version matches the packaged application manifest",()=>assert.equal(appInfo.version,require("../package.json").version));
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
  await assert.rejects(checkPublishedRelease({fetcher:async()=>response(403)}),/频率受限/);
  await assert.rejects(checkPublishedRelease({fetcher:async()=>response(503)}),/暂时无法/);
  await assert.rejects(checkPublishedRelease({fetcher:async()=>{throw new Error("network");}}),/network/);
});
test("update checks omit credentials and only accept verified project release links",async()=>{
  await checkPublishedRelease({fetcher:async(url,options)=>{assert.equal(url,appInfo.releaseApi);assert.equal(options.credentials,"omit");assert.equal(options.headers.Authorization,undefined);return response(404);}});
  for(const value of [{...release("v0.4.3"),html_url:"https://untrusted.example/download"},{...release("v0.4.3"),prerelease:true},release("unknown-version")])await assert.rejects(checkPublishedRelease({fetcher:async()=>response(200,value)}),/无法核验/);
});
test("browser and native project links share the same restricted boundary",()=>{
  for(const url of [appInfo.repository,appInfo.repository+"/releases",appInfo.repository+"/releases/tag/v0.4.3"]){assert.equal(allowedProjectLink(url),true);assert.equal(nativeLinks.allowedProjectLink(url),true);}
  for(const url of ["file:///C:/Windows/notepad.exe","javascript:alert(1)","https://github.com.evil.example/ravicc02/ai-quiz-assistant","https://github.com/other/repository",appInfo.repository+"-other",appInfo.repository+"?secret=private"]){assert.equal(allowedProjectLink(url),false);assert.equal(nativeLinks.allowedProjectLink(url),false);}
});
test("automatic checks are opt-in and survive a local reload",()=>{
  let value=null;const store={getItem:()=>value,setItem:(_key,next)=>value=next};
  assert.equal(loadUpdatePreference(store),false);saveUpdatePreference(true,store);assert.equal(loadUpdatePreference(store),true);
  value="invalid";assert.equal(loadUpdatePreference(store),false);
});
