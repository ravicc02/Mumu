<script setup>
import { computed, nextTick, ref, watch } from "vue";
import { BookOpen, Check, ClipboardList, Cpu, Database, Globe2, Info, Search, Settings2, UserRound, X } from "@lucide/vue";
import { filterSettingsNavigation, settingsNavigation } from "../lib/navigation.js";
import LocalAccountSettings from "../../accounts/components/LocalAccountSettings.vue";
import AboutSettings from "./AboutSettings.vue";
import SettingsWorkspace from "./SettingsWorkspace.vue";
import DataSettings from "./DataSettings.vue";
const props=defineProps({open:Boolean,section:{type:String,default:"account"},accounts:{type:Array,default:()=>[]},info:Object,profile:{type:Object,required:true},saveProfile:{type:Function,required:true},update:{type:Object,required:true},autoCheck:Boolean,checkUpdates:Function,setAutoCheck:Function,openExternal:Function,openData:Function,openLogs:Function});
const emit=defineEmits(["close","update:section","saved","model-saved","platforms"]);
const icons={account:UserRound,model:Cpu,assignments:ClipboardList,learning:BookOpen,attendance:Globe2,system:Settings2,data:Database,about:Info};
const dialog=ref(null),content=ref(null),query=ref(""),initialized=ref(false),configurationSection=ref("model"),feedback=ref(""),feedbackError=ref(false);
const current=computed(()=>settingsNavigation.find(item=>item.id===props.section)||settingsNavigation[0]);
const filtered=computed(()=>filterSettingsNavigation(query.value));
const groups=computed(()=>[...new Set(filtered.value.map(item=>item.group))]);
const isConfiguration=computed(()=>["model","assignments","learning","attendance","system"].includes(current.value.id));
const isData=computed(()=>current.value.id==="data");
let trigger;
watch(()=>props.open,async open=>{if(open){trigger=document.activeElement;initialized.value=true;query.value="";await nextTick();dialog.value?.focus();}else if(trigger?.isConnected)trigger.focus();},{immediate:true});
watch(()=>props.section,async section=>{if(["model","assignments","learning","attendance","system"].includes(section))configurationSection.value=section;feedback.value="";await nextTick();if(content.value)content.value.scrollTop=0;},{immediate:true});
watch(query,()=>{if(filtered.value.length&&!filtered.value.some(item=>item.id===props.section))emit("update:section",filtered.value[0].id);});
function trap(event){const items=[...dialog.value.querySelectorAll('button:not(:disabled),input:not(:disabled),select:not(:disabled),a[href],textarea:not(:disabled)')].filter(item=>item.getClientRects().length);const first=items[0],last=items.at(-1);if(event.shiftKey&&[first,dialog.value].includes(document.activeElement)){event.preventDefault();last?.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}}
async function directory(kind){feedback.value="";feedbackError.value=false;try{const ok=await(kind==="data"?props.openData:props.openLogs)();if(!ok)throw new Error("目录操作未完成，请检查本地服务与剪贴板权限。");feedback.value=(kind==="logs"||window.desktopApi)?"已打开"+(kind==="data"?"数据":"日志")+"目录":"已复制"+(kind==="data"?"数据":"日志")+"目录";return true;}catch(error){feedbackError.value=true;feedback.value=error.message;return false;}}
</script>
<template>
  <Teleport to="body">
    <Transition name="settings-dialog">
      <div v-if="initialized" v-show="open" class="modal-backdrop settings-backdrop" @mousedown.self="emit('close')" @keydown.esc.stop="emit('close')" @keydown.tab.stop="trap">
        <section id="application-settings-dialog" ref="dialog" class="modal-card application-settings-dialog" role="dialog" aria-modal="true" aria-labelledby="application-settings-title" tabindex="-1">
          <aside class="application-settings-sidebar">
            <div class="application-settings-brand">
              <span class="application-settings-mark"><img src="/brand/mumu.png" width="26" height="26" alt="" /></span>
              <div><span>Mumu</span><h1>设置</h1></div>
            </div>
            <label class="search-field application-settings-search">
              <Search :size="15" />
              <input v-model="query" type="search" placeholder="搜索设置" aria-label="搜索设置">
            </label>
            <nav aria-label="设置分类">
              <section v-for="group in groups" :key="group || 'primary'">
                <h3 v-if="group">{{group}}</h3>
                <button
                  v-for="item in filtered.filter(item=>item.group===group)"
                  :key="item.id"
                  class="settings-section-button"
                  :class="{active:section===item.id}"
                  type="button"
                  :aria-current="section===item.id?'page':undefined"
                  @click="emit('update:section',item.id)"
                >
                  <span class="settings-nav-icon"><component :is="icons[item.id]" :size="16" /></span>
                  <span>{{item.label}}</span>
                </button>
              </section>
              <p v-if="!filtered.length" class="settings-no-match">没有匹配设置</p>
            </nav>
          </aside>
          <div class="application-settings-main">
            <header class="application-settings-header">
              <div class="application-settings-title">
                <div>
                  <h2 id="application-settings-title">{{current.id==='account'?'本地账户':current.label}}</h2>
                </div>
              </div>
              <button class="icon-button settings-close" type="button" aria-label="关闭设置" title="关闭" @click="emit('close')"><X :size="19" /></button>
            </header>
            <div v-if="feedback" class="settings-directory-feedback" :class="{error:feedbackError}" role="status">
              <Check v-if="!feedbackError" :size="14" />{{feedback}}
            </div>
            <div ref="content" class="application-settings-content" :class="{'about-open':current.id==='about'}">
              <div v-if="!filtered.length" class="settings-search-empty">
                <Search :size="26" />
                <h3>没有找到设置</h3>
                <button class="button light" type="button" @click="query=''">清除搜索</button>
              </div>
              <div v-show="filtered.length" class="settings-content-sections">
                <LocalAccountSettings v-show="current.id==='account'" :profile="profile" :save-profile="saveProfile" />
                <KeepAlive>
                  <SettingsWorkspace
                    v-if="isConfiguration"
                    embedded
                    :auto-check="autoCheck"
                    :set-auto-check="setAutoCheck"
                    :section="configurationSection"
                    :accounts="accounts"
                    :info="info"
                    @update:section="emit('update:section',$event)"
                    @saved="emit('saved',$event)"
                    @model-saved="emit('model-saved')"
                    @open-data="directory('data')"
                    @open-logs="directory('logs')"
                    @platforms="emit('platforms')"
                  />
                </KeepAlive>
                <DataSettings
                  v-if="isData"
                  :info="info"
                  :open-data="()=>directory('data')"
                  :open-logs="()=>directory('logs')"
                />
                <AboutSettings
                  v-show="current.id==='about'"
                  :update="update"
                  :visible="open && current.id==='about'"
                  :auto-check="autoCheck"
                  :set-auto-check="setAutoCheck"
                  :check-updates="checkUpdates"
                  :open-external="openExternal"
                  :open-logs="()=>directory('logs')"
                  :close="()=>emit('close')"
                />
              </div>
            </div>
          </div>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>
<style scoped>
/* 设置框架沿用统一事务型画布与导航规范。 */
.settings-backdrop{z-index:90;padding:24px;background:rgba(23,31,42,.38);}.application-settings-dialog{display:grid;grid-template-columns:216px minmax(0,1fr);width:min(1220px,calc(100vw - 48px));height:min(840px,calc(100dvh - 48px));max-height:calc(100dvh - 48px);padding:0;overflow:hidden;outline:none;background:var(--surface);border:1px solid rgba(23,31,42,.18);border-radius:16px;box-shadow:0 28px 80px -20px rgba(20,30,42,.30);}.application-settings-sidebar{display:flex;flex-direction:column;gap:14px;min-height:0;padding:16px 12px;border-right:1px solid var(--line);background:#f2f4f7;}.application-settings-brand{display:flex;align-items:center;gap:10px;min-height:44px;padding:0 8px;}.application-settings-mark{display:grid;place-items:center;width:36px;height:36px;border:1px solid var(--line);border-radius:10px;background:var(--surface);}.application-settings-brand>div{display:grid;gap:1px;}.application-settings-brand span{color:var(--muted);font-size:11px;line-height:1.2;}.application-settings-brand h1{font-size:18px;line-height:1.2;font-weight:500;}.application-settings-search{width:100%;height:36px;background:#fafbfc;}.application-settings-sidebar nav{overflow-y:auto;min-height:0;padding:0 2px 4px;}.application-settings-sidebar nav section+section{margin-top:14px;}.application-settings-sidebar nav h3{font-size:11px;color:var(--subtle);font-weight:500;letter-spacing:0;margin:0 10px 5px;}.settings-section-button{position:relative;width:100%;display:flex;align-items:center;gap:10px;height:36px;min-width:0;border:1px solid transparent;border-radius:8px;background:transparent;color:var(--muted);padding:0 9px;text-align:left;font-size:var(--text-sm);transition:background 160ms var(--ease),color 160ms var(--ease),border-color 160ms var(--ease);}.settings-section-button+.settings-section-button{margin-top:2px;}.settings-nav-icon{display:grid;place-items:center;width:20px;height:20px;flex:none;color:var(--subtle);}.settings-section-button.active{background:var(--accent-soft);border-color:rgba(var(--accent-rgb),.16);color:var(--ink);}.settings-section-button.active .settings-nav-icon{color:var(--accent);}.application-settings-main{display:flex;flex-direction:column;min-width:0;min-height:0;background:#f7f8fa;}.application-settings-header{display:flex;align-items:center;justify-content:space-between;gap:20px;min-height:76px;padding:16px 32px;border-bottom:1px solid var(--line);background:#fbfcfd;}.application-settings-title{display:flex;align-items:center;gap:12px;min-width:0;}.application-settings-title-icon{display:grid;place-items:center;width:38px;height:38px;flex:none;border:1px solid rgba(var(--accent-rgb),.15);border-radius:10px;background:var(--accent-soft);color:var(--accent);}.application-settings-title h2{font-size:19px;line-height:1.35;font-weight:500;}.application-settings-title p{margin-top:2px;color:var(--muted);font-size:12px;line-height:1.5;}.settings-close{flex:none;border:1px solid transparent;}.application-settings-content{flex:1;min-height:0;overflow:auto;overscroll-behavior:contain;padding:24px 32px 32px;scrollbar-gutter:stable;}.application-settings-content.about-open .settings-content-sections{height:100%;}.application-settings-content .settings-section-stack{max-width:920px;margin:0 auto;}.settings-directory-feedback{display:flex;align-items:center;gap:7px;color:var(--green);font-size:var(--text-xs);padding:10px 32px 0;background:#fbfcfd;border-bottom:1px solid var(--line);}.settings-directory-feedback.error{color:var(--red);}.settings-no-match{padding:10px;color:var(--muted);font-size:var(--text-xs);}.settings-search-empty{display:flex;align-items:center;justify-content:center;flex-direction:column;gap:16px;height:100%;color:var(--muted);}.settings-search-empty h3{font-size:15px;font-weight:500;}.application-settings-dialog button:focus-visible,.application-settings-sidebar input:focus-visible{outline:2px solid var(--accent);outline-offset:2px;}.settings-section-button:active{transform:translateY(1px);}@media(hover:hover){.settings-section-button:not(.active):hover{color:var(--ink);background:rgba(255,255,255,.58);}.settings-close:hover{border-color:var(--line);background:var(--surface-muted);}}@media(max-width:900px){.settings-backdrop{padding:16px;}.application-settings-dialog{width:calc(100vw - 32px);height:calc(100dvh - 32px);max-height:calc(100dvh - 32px);grid-template-columns:188px minmax(0,1fr);}.application-settings-sidebar{padding:14px 10px;}.application-settings-header{padding:14px 22px;}.application-settings-content{padding:20px 22px 24px;}}.settings-dialog-enter-active,.settings-dialog-leave-active{transition:opacity 180ms var(--ease);}.settings-dialog-enter-active .application-settings-dialog,.settings-dialog-leave-active .application-settings-dialog{transition:transform 220ms var(--ease),opacity 180ms var(--ease);}.settings-dialog-enter-from,.settings-dialog-leave-to{opacity:0;}.settings-dialog-enter-from .application-settings-dialog,.settings-dialog-leave-to .application-settings-dialog{transform:translateY(8px);opacity:0;}@media(prefers-reduced-motion:reduce){.settings-dialog-enter-active,.settings-dialog-leave-active,.settings-dialog-enter-active .application-settings-dialog,.settings-dialog-leave-active .application-settings-dialog,.settings-section-button{transition:none;}}
.application-settings-dialog .application-settings-sidebar{background:var(--bg);}
.application-settings-dialog .application-settings-search{background:var(--surface);}
.application-settings-dialog .application-settings-main{background:var(--bg);}
.application-settings-dialog .application-settings-sidebar nav{scrollbar-gutter:stable;}
.application-settings-dialog .settings-section-button{min-height:40px;height:40px;border-radius:var(--radius-pill);}
.application-settings-dialog .application-settings-title h2{font-family:var(--font-display);font-size:24px;font-weight:330;}
.application-settings-dialog .application-settings-title-icon{border-radius:50%;border-color:transparent;}
.application-settings-dialog .settings-section-button[aria-current="page"]{font-weight:600;}
.application-settings-dialog .application-settings-content{background:var(--bg);}
.application-settings-dialog .application-settings-header{min-height:76px;padding:24px 32px 12px;border-bottom:0;background:var(--bg);}
.application-settings-dialog .application-settings-title-icon{background:var(--accent-soft);}
.application-settings-dialog .settings-search-empty{min-height:240px;}
@media (prefers-reduced-motion:reduce){.application-settings-dialog .settings-section-button{transition:none;}}
</style>
