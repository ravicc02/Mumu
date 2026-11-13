import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { parse, compileScript } from "@vue/compiler-sfc";
import { createRenderer, defineComponent, h, nextTick } from "vue";

const { descriptor }=parse(await readFile(new URL("../src/components/LiveBrowser.vue",import.meta.url),"utf8"));
let source=compileScript(descriptor,{id:"live-test",inlineTemplate:true}).content;
for(const name of ["vue","@lucide/vue"]) source=source.replaceAll('from "'+name+'"','from '+JSON.stringify(import.meta.resolve(name)));
source=source.replace('from "../api"','from '+JSON.stringify("data:text/javascript,"+encodeURIComponent('export const api={browserSession:async()=>({token:"fixture"}),browserUrl:()=>"ws://fixture.test"};')));
source=source.replace('from "../lib/task-presentation"','from '+JSON.stringify(new URL("../src/lib/task-presentation.js",import.meta.url).href));
const {default:Viewer}=await import("data:text/javascript;base64,"+Buffer.from(source).toString("base64"));

const node=type=>({type,children:[],props:{},parent:null,width:0,height:0,getContext:()=>({drawImage(){}}),getBoundingClientRect:()=>({left:0,top:0,width:1000,height:600}),focus(){},setPointerCapture(){},hasPointerCapture(){return false;}});
const renderer=createRenderer({
  createElement:node,createText:text=>({...node("text"),text}),createComment:text=>({...node("comment"),text}),
  setText:(item,text)=>{item.text=text;},setElementText:(item,text)=>{item.text=text;},
  parentNode:item=>item.parent,nextSibling:item=>item.parent?.children[item.parent.children.indexOf(item)+1],
  patchProp:(item,key,previous,value)=>{item.props[key]=value;},
  insert(item,parent,anchor){if(item.parent)item.parent.children.splice(item.parent.children.indexOf(item),1);const index=anchor?parent.children.indexOf(anchor):-1;parent.children.splice(index<0?parent.children.length:index,0,item);item.parent=parent;},
  remove(item){item.parent?.children.splice(item.parent.children.indexOf(item),1);item.parent=null;},
});
const find=(root,predicate)=>predicate(root)?root:root.children.map(item=>find(item,predicate)).find(Boolean);

async function mountViewer({ kind = "assignment", nativeBrowser = true } = {}) {
  const previous={Image:globalThis.Image,WebSocket:globalThis.WebSocket,ResizeObserver:globalThis.ResizeObserver};
  const images=[],sockets=[];
  globalThis.Image=class {constructor(){this.naturalWidth=1440;this.naturalHeight=900;images.push(this);}set src(value){this.source=value;}};
  globalThis.WebSocket=class {static OPEN=1;constructor(){this.readyState=1;this.sent=[];sockets.push(this);}send(value){this.sent.push(JSON.parse(value));}close(){this.readyState=3;this.onclose?.();}receive(value){this.onmessage?.({data:typeof value==="string"?value:JSON.stringify(value)});}};
  globalThis.ResizeObserver=class {constructor(callback){this.callback=callback;}observe(){this.callback([{contentRect:{width:1000,height:600}}]);}disconnect(){}};
  const root=node("root"),app=renderer.createApp(defineComponent({setup:()=>()=>h(Viewer,{runId:"job",kind,nativeBrowser})}));
  app.mount(root);await nextTick();await new Promise(resolve=>setImmediate(resolve));
  const socket=sockets[0];socket.onopen();
  return {root,images,socket,state:()=>root.children[0].props["data-stream-state"],close(){app.unmount();Object.assign(globalThis,previous);}};
}
const frame=view_id=>({type:"frame",image:"fixture",url:"https://fixture.test/work",view_id});

test("closing a headless task viewer only closes its stream and never sends a task stop",async()=>{
  const viewer=await mountViewer({kind:"learning",nativeBrowser:false});
  try {
    viewer.socket.receive(frame(1));
    viewer.images[0].onload();await nextTick();
    assert.equal(viewer.state(),"connected");
    assert.equal(find(viewer.root,item=>item.props.title==="进入原浏览器"),undefined);
    assert.deepEqual(viewer.socket.sent,[{token:"fixture"}]);
  } finally {viewer.close();}
  assert.equal(viewer.socket.readyState,3);
  assert.deepEqual(viewer.socket.sent,[{token:"fixture"}]);
});

test("a late decoded image cannot reconnect a closed task browser",async()=>{
  const viewer=await mountViewer();
  try {
    viewer.socket.receive(frame(1));
    viewer.socket.receive({type:"closed",message:"任务浏览器已关闭"});
    viewer.images[0].onload();await nextTick();
    assert.equal(viewer.state(),"closed");
    assert.equal(find(viewer.root,item=>item.type==="canvas").props["data-frame-sequence"],0);
  } finally {viewer.close();}
});

test("switching pages cancels old decoding and sends input only for the displayed page",async()=>{
  const viewer=await mountViewer();
  try {
    viewer.socket.receive(frame(1));viewer.images[0].onload();await nextTick();
    viewer.socket.receive(frame(1));
    viewer.socket.receive({type:"switching",message:"正在切换任务页面"});
    viewer.images[1].onload();await nextTick();
    assert.equal(viewer.state(),"connecting");
    viewer.socket.receive(frame(2));viewer.images[2].onload();await nextTick();
    const canvas=find(viewer.root,item=>item.type==="canvas");
    assert.equal(canvas.props["data-frame-sequence"],2);
    assert.equal(canvas.props["data-view-id"],2);
    canvas.props.onWheel({clientX:200,clientY:120,deltaMode:0,deltaX:0,deltaY:600,preventDefault(){}});
    assert.deepEqual(viewer.socket.sent.at(-1),{type:"wheel",x:.2,y:.2,delta_x:0,delta_y:600,modifiers:0,view_id:2});
  } finally {viewer.close();}
});

test("invalid stream payload disconnects with feedback instead of an uncaught parsing error",async()=>{
  const viewer=await mountViewer();
  try {viewer.socket.receive("{invalid");await nextTick();assert.equal(viewer.state(),"disconnected");}
  finally {viewer.close();}
});

test("network disconnect invalidates a pending decode and cannot show a false online state",async()=>{
  const viewer=await mountViewer();
  try {
    viewer.socket.receive(frame(1));
    viewer.socket.close();viewer.images[0].onload();await nextTick();
    assert.equal(viewer.state(),"disconnected");
  } finally {viewer.close();}
});

test("a JSON scalar is reported as invalid data instead of throwing in the UI",async()=>{
  const viewer=await mountViewer();
  try {viewer.socket.receive("null");await nextTick();assert.equal(viewer.state(),"disconnected");}
  finally {viewer.close();}
});

test("Chinese composition commits once and retains the current page identity",async()=>{
  const viewer=await mountViewer();
  try {
    viewer.socket.receive(frame(3));viewer.images[0].onload();await nextTick();
    const keyboard=find(viewer.root,item=>item.type==="textarea");
    const target={value:"中文输入"};
    keyboard.props.onInput({isComposing:true,target});
    assert.equal(viewer.socket.sent.length,1);
    keyboard.props.onCompositionend({isComposing:false,target});
    keyboard.props.onInput({isComposing:false,target});
    assert.deepEqual(viewer.socket.sent.at(-1),{type:"text",text:"中文输入",view_id:3});
    assert.equal(viewer.socket.sent.length,2);
  } finally {viewer.close();}
});
