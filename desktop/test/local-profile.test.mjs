import { test } from "node:test";
import assert from "node:assert/strict";
import { avatarFromFile, loadLocalProfile, localProfileKey, normalizeLocalProfile, saveLocalProfile } from "../src/features/accounts/lib/local-profile.js";
function storage(){const values=new Map();return{values,getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value)};}

test("local profile persists without changing platform account preferences",()=>{
  const store=storage();store.setItem("studio:account","platform-account-id");
  const value=saveLocalProfile({nickname:" 本地使用者 ",email:" user@example.test ",avatar:"",platform_user_id:"do-not-copy"},store);
  assert.deepEqual(value,{nickname:"本地使用者",email:"user@example.test",avatar:""});
  assert.deepEqual(loadLocalProfile(store),value);
  assert.equal(store.getItem("studio:account"),"platform-account-id");
  assert.ok(!store.getItem(localProfileKey).includes("platform_user_id"));
});
test("invalid nickname, email, or remote avatar cannot overwrite saved local profile",()=>{
  const store=storage(),original=saveLocalProfile({nickname:"Original",email:"",avatar:""},store);
  for(const value of [{nickname:" ",email:""},{nickname:"Valid",email:"invalid"},{nickname:"Valid",email:"",avatar:"https://example.test/photo.jpg"}])assert.throws(()=>saveLocalProfile(value,store));
  assert.deepEqual(loadLocalProfile(store),original);
  assert.equal(normalizeLocalProfile({avatar:"javascript:alert(1)"}).avatar,"");
});
test("corrupt preferences recover to a local default and failed writes report failure",()=>{
  const store=storage();store.setItem(localProfileKey,"{broken");
  assert.deepEqual(loadLocalProfile(store),{nickname:"本地用户",email:"",avatar:""});
  assert.throws(()=>saveLocalProfile({nickname:"Name",email:"",avatar:""},{setItem(){throw new Error("quota");}}),/保存失败/);
});
test("avatar conversion uses the center square and frees the decoded bitmap",async()=>{
  const file=new File([new Uint8Array([137,80,78,71,13,10,26,10,0,0,0,0])],"avatar.png",{type:"image/png"});
  const calls=[];let closed=false;
  const bitmap={width:400,height:200,close(){closed=true;}};
  const canvas={getContext:()=>({drawImage:(...args)=>calls.push(args)}),toDataURL:()=>"data:image/webp;base64,UklGRg=="};
  const result=await avatarFromFile(file,{decode:async()=>bitmap,canvasFactory:()=>canvas});
  assert.equal(canvas.width,256);assert.equal(canvas.height,256);
  assert.deepEqual(calls[0].slice(1),[100,0,200,200,0,0,256,256]);
  assert.equal(result,"data:image/webp;base64,UklGRg==");assert.equal(closed,true);
});
test("non-image and oversized avatar inputs fail before image decoding",async()=>{
  let decoded=false;
  await assert.rejects(avatarFromFile(new File(["<svg></svg>"],"fake.png",{type:"image/png"}),{decode:async()=>{decoded=true;}}),/支持 PNG/);
  await assert.rejects(avatarFromFile({size:6*1024*1024},{decode:async()=>{decoded=true;}}),/5 MB/);
  assert.equal(decoded,false);
});
