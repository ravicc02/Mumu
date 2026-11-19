<script setup>
import ToggleSwitch from "../../../shared/controls/ToggleSwitch.vue";
import { computed, onMounted, ref, watch } from "vue";
import { BookOpen, Check, ClipboardList, Cpu, Globe2, LoaderCircle, RotateCcw, Save, Settings2 } from "@lucide/vue";
import { api } from "../../../app/api.js";
import ModelConfiguration from "./ModelConfiguration.vue";
import AttendanceDomainSettings from "./AttendanceDomainSettings.vue";
import CommonSettings from "./CommonSettings.vue";

const props = defineProps({ accounts: { type: Array, default: () => [] }, info: Object, section: { type: String, default: "model" }, embedded: Boolean, autoCheck: Boolean, setAutoCheck: Function });
const emit = defineEmits(["saved", "model-saved", "update:section", "open-data", "open-logs", "platforms"]);
const tabs = [{ id: "model", label: "模型配置", icon: Cpu }, { id: "assignments", label: "作业配置", icon: ClipboardList }, { id: "learning", label: "网课配置", icon: BookOpen }, { id: "attendance", label: "签到域名", icon: Globe2 }, { id: "system", label: "系统设置", icon: Settings2 }];
const current = computed(() => tabs.find(item => item.id === props.section) || tabs[0]);
const preferences = ref(null);
const originals = ref({});
const loading = ref(true);
const loadError = ref("");
const pending = ref("");
const messages = ref({});
const errors = ref({});
const draftKey = "studio:settings-draft:v1";
function snapshot(section) { return JSON.stringify(preferences.value?.[section]); }
function dirty(section) { return Boolean(preferences.value && Object.hasOwn(originals.value, section) && originals.value[section] !== snapshot(section)); }
const validation = computed(() => "");
function cache() {
  if (loading.value || !preferences.value) return;
  const draft = {};
  for (const section of ["assignments", "learning", "system"]) {
    if (dirty(section)) draft[section] = preferences.value[section];
  }
  try { Object.keys(draft).length ? localStorage.setItem(draftKey, JSON.stringify(draft)) : localStorage.removeItem(draftKey); } catch { /* Storage may be unavailable. */ }
}
watch(preferences, cache, { deep: true });
onMounted(load);
async function load() {
  loading.value = true; loadError.value = "";
  try {
    const result = await api.preferences();
    result.assignments.interactive = true;
    preferences.value = result;
    for (const section of ["assignments", "learning", "system"]) originals.value[section] = snapshot(section);
    let cached = null;
    try { cached = JSON.parse(localStorage.getItem(draftKey) || "null"); } catch { cached = null; }
    if (cached && typeof cached === "object") {
      for (const section of ["assignments", "learning", "system"]) {
        if (cached[section] && typeof cached[section] === "object") {
          preferences.value[section] = { ...preferences.value[section], ...cached[section] };
        }
      }
      preferences.value.assignments.interactive = true;
    }
  } catch (error) { loadError.value = error.message; }
  finally { loading.value = false; }
}
async function save() {
  const section = current.value.id;
  if (pending.value || validation.value) return;
  pending.value = section; messages.value[section] = ""; errors.value[section] = "";
  try {
    const payload = { ...preferences.value[section] };
    if (section === "system") {
      const latest = await api.preferences();
      payload.log_retention_days = latest.system.log_retention_days;
      payload.log_size_mb = latest.system.log_size_mb;
    }
    const result = await api.savePreferences({ [section]: payload });
    originals.value[section] = snapshot(section);
    messages.value[section] = "已保存";
    cache(); emit("saved", { preferences: result, section });
  } catch (error) { errors.value[section] = error.message; }
  finally { pending.value = ""; }
}
function reset() {
  const section = current.value.id;
  const original = JSON.parse(originals.value[section]);
  preferences.value[section] = original;
  messages.value[section] = ""; errors.value[section] = ""; cache();
}
</script>

<template>
  <main class="settings-workspace" :class="{ 'settings-embedded': embedded }">
    <aside v-if="!embedded" class="preferences-nav">
      <h2>设置</h2>
      <nav aria-label="设置分类">
        <button
          v-for="tab in tabs"
          :key="tab.id"
          type="button"
          :aria-label="tab.label"
          :aria-current="current.id === tab.id ? 'page' : undefined"
          :class="{ active: current.id === tab.id }"
          @click="emit('update:section', tab.id)"
        >
          <component :is="tab.icon" :size="16" />
          <span>{{ tab.label }}</span>
          <span v-if="dirty(tab.id)" class="unsaved-dot" title="未保存" aria-hidden="true" />
        </button>
      </nav>
    </aside>

    <div class="preferences-content">
      <header v-if="!embedded" class="preferences-title">
        <component :is="current.icon" :size="20" />
        <h2>{{ current.label }}</h2>
      </header>

      <div v-show="current.id === 'model'" class="preferences-body settings-section-stack">
        <ModelConfiguration @saved="emit('model-saved')" />
      </div>
      <div v-if="current.id === 'attendance'" class="preferences-body settings-section-stack">
        <AttendanceDomainSettings />
      </div>
      <Transition v-else name="preference-switch" mode="out-in">
        <div v-if="current.id !== 'model'" :key="current.id" class="preferences-body settings-section-stack">
          <div v-if="loading" class="settings-loading" role="status">
            <LoaderCircle class="spinning" :size="18" />正在读取配置
          </div>
          <div v-else-if="loadError" class="settings-load-error" role="alert">
            {{ loadError }}
            <button class="button light" type="button" @click="load"><RotateCcw :size="14" />重试</button>
          </div>
          <template v-else-if="preferences">
          <CommonSettings v-if="current.id === 'system'" :auto-check="autoCheck" :set-auto-check="setAutoCheck" />
          <form @submit.prevent="save" :aria-busy="Boolean(pending)">
            <fieldset :disabled="Boolean(pending)">
              <template v-if="current.id === 'assignments'">
                <section class="settings-card">
                  <header class="settings-card-heading">
                    <div class="settings-card-title">
                      <h3>执行策略</h3>
                    </div>
                  </header>
                  <div class="settings-card-body">
                    <div class="settings-control-grid">
                      <label class="settings-control-cell">
                        <span class="settings-control-copy">
                          <strong>Agent 最大步骤</strong>
                          <small>单个作业允许执行的最大步骤数</small>
                        </span>
                        <span class="settings-number-wrap">
                          <input v-model.number="preferences.assignments.max_steps" class="settings-number" type="number" required min="10" max="500" step="1">
                          <span>步</span>
                        </span>
                      </label>
                      <label class="settings-control-cell">
                        <span class="settings-control-copy">
                          <strong>自动执行并发</strong>
                          <small>同时运行的自动作业任务数量</small>
                        </span>
                        <span class="settings-number-wrap">
                          <input v-model.number="preferences.assignments.concurrency" class="settings-number" type="number" required min="1" max="8" step="1">
                          <span>个任务</span>
                        </span>
                      </label>
                    </div>
                  </div>
                </section>

              </template>

              <template v-else-if="current.id === 'learning'">
                <section class="settings-card">
                  <header class="settings-card-heading">
                    <div class="settings-card-title">
                      <h3>浏览器与播放</h3>
                    </div>
                    <div class="settings-card-heading-actions">
                      <span class="settings-scope">新任务生效</span>
                    </div>
                  </header>
                  <div class="settings-card-body">
                    <label class="preference-row">
                      <span>
                        <strong>后台运行</strong>
                        <small>{{ preferences.learning.headless ? '不弹出窗口，通过软件内实时画面操控。' : '显示独立浏览器窗口，关闭窗口会中断任务。' }}</small>
                      </span>
                      <ToggleSwitch v-model="preferences.learning.headless" role="switch" aria-label="网课后台运行" />
                    </label>
                    <div class="settings-control-grid">
                      <label class="settings-control-cell">
                        <span class="settings-control-copy">
                          <strong>播放倍速</strong>
                          <small>新任务开始播放时使用</small>
                        </span>
                        <select v-model.number="preferences.learning.playback_rate" class="settings-select" aria-label="播放倍速">
                          <option v-for="rate in [0.5, 0.75, 1, 1.25, 1.5, 2]" :key="rate" :value="rate">{{ rate }} ×</option>
                        </select>
                      </label>
                      <label class="settings-control-cell">
                        <span class="settings-control-copy">
                          <strong>音量</strong>
                          <small>0% 到 100%</small>
                        </span>
                        <span class="settings-range-wrap">
                          <input v-model.number="preferences.learning.volume" type="range" min="0" max="100" step="1" aria-label="音量">
                          <output>{{ preferences.learning.volume }}%</output>
                        </span>
                      </label>
                    </div>
                  </div>
                </section>

                <section class="settings-card">
                  <header class="settings-card-heading">
                    <div class="settings-card-title">
                      <h3>等待与核验</h3>
                    </div>
                  </header>
                  <div class="settings-card-body">
                    <div class="settings-control-grid three">
                      <label class="settings-control-cell">
                        <span class="settings-control-copy">
                          <strong>资源加载等待</strong>
                          <small>控件未出现时的等待时间</small>
                        </span>
                        <span class="settings-number-wrap">
                          <input v-model.number="preferences.learning.resource_wait_seconds" class="settings-number" type="number" required min="5" max="120" step="1">
                          <span>秒</span>
                        </span>
                      </label>
                      <label class="settings-control-cell">
                        <span class="settings-control-copy">
                          <strong>平台完成确认</strong>
                          <small>提交后等待进度回写的上限</small>
                        </span>
                        <span class="settings-number-wrap">
                          <input v-model.number="preferences.learning.confirmation_wait_seconds" class="settings-number" type="number" required min="10" max="180" step="1">
                          <span>秒</span>
                        </span>
                      </label>
                      <label class="settings-control-cell">
                        <span class="settings-control-copy">
                          <strong>播放无进展判定</strong>
                          <small>连续停滞多久后处理播放器</small>
                        </span>
                        <span class="settings-number-wrap">
                          <input v-model.number="preferences.learning.stall_seconds" class="settings-number" type="number" required min="30" max="300" step="1">
                          <span>秒</span>
                        </span>
                      </label>
                    </div>
                  </div>
                </section>
              </template>

              <template v-else-if="current.id === 'system'">
                <section class="settings-card">
                  <header class="settings-card-heading">
                    <div class="settings-card-title">
                      <h3>调度</h3>
                    </div>
                  </header>
                  <div class="settings-card-body">
                    <label class="preference-row">
                      <span>
                        <strong>自动检测并发</strong>
                        <small>同时检测的账号数量，避免平台请求过于集中。</small>
                      </span>
                      <span class="settings-number-wrap">
                        <input v-model.number="preferences.system.collection_concurrency" class="settings-number" type="number" required min="1" max="8" step="1">
                        <span>个账号</span>
                      </span>
                    </label>
                  </div>
                </section>

              </template>
            </fieldset>
            <footer class="settings-action-bar preferences-save-bar">
              <span class="preferences-feedback" :class="{ error: validation || errors[current.id] }" role="status">
                <span v-if="validation || errors[current.id] || dirty(current.id)" class="settings-feedback-dot" :class="{ dirty: dirty(current.id), error: validation || errors[current.id] }" aria-hidden="true" />
                <Check v-if="messages[current.id] && !dirty(current.id)" :size="14" />
                {{ validation || errors[current.id] || (dirty(current.id) ? '有未保存的更改' : messages[current.id]) }}
              </span>
              <button class="button light" type="button" :disabled="!dirty(current.id) || Boolean(pending)" @click="reset">
                <RotateCcw :size="14" />撤销
              </button>
              <button class="button dark" type="submit" :disabled="!dirty(current.id) || Boolean(validation) || Boolean(pending)">
                <LoaderCircle v-if="pending === current.id" class="spinning" :size="14" />
                <Save v-else :size="14" />保存配置
              </button>
            </footer>
          </form>
          </template>
        </div>
      </Transition>
    </div>
  </main>
</template>
