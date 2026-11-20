<script setup>
import { computed, onMounted, ref } from "vue";
import { Check, Download, Eye, EyeOff, GripVertical, LoaderCircle, Pencil, Plus, Save, Trash2, Zap } from "@lucide/vue";
import { api } from "../../../app/api.js";
import ModelUsage from "./ModelUsage.vue";

const emit = defineEmits(["saved"]);
const activeTab = ref("model");

function emptyProvider() {
  return {
    id: "",
    name: "",
    base_url: "",
    api_key: "",
    api_key_configured: false,
    api_format: "openai",
    use_responses_api: false,
    models: [],
  };
}

const providers = ref([]);
const selectedIndex = ref(0);
const original = ref("");
const catalog = ref([]);
const catalogSource = ref(-1);
const modelName = ref("");
const addingModel = ref(false);
const editingModel = ref(-1);
const editedModelName = ref("");
const revealed = ref(false);
const loading = ref(true);
const loadFailed = ref(false);
const pending = ref("");
const message = ref("");
const failed = ref(false);
const providerDrag = ref(-1);
const modelDrag = ref(-1);

const selected = computed(() => providers.value[selectedIndex.value] || null);
const keyReady = computed(() => Boolean(selected.value && (selected.value.api_key_configured || selected.value.api_key.trim())));
const fetchReady = computed(() => Boolean(selected.value && /^https?:\/\//.test(selected.value.base_url.trim()) && keyReady.value));
const valid = computed(() => providers.value.length > 0 && providers.value.every(provider => (
  provider.name.trim()
  && /^https?:\/\//.test(provider.base_url.trim())
  && provider.models.length > 0
  && (provider.api_key_configured || provider.api_key.trim())
)));
const dirty = computed(() => JSON.stringify(snapshot()) !== original.value);
const activeCatalog = computed(() => (catalogSource.value === selectedIndex.value ? catalog.value : []));

function snapshot() {
  return {
    providers: providers.value.map(provider => ({
      id: provider.id || "",
      name: provider.name,
      base_url: provider.base_url,
      api_format: provider.api_format,
      use_responses_api: Boolean(provider.use_responses_api),
      models: [...provider.models],
      api_key: provider.api_key || "",
    })),
  };
}
onMounted(load);

async function load() {
  loading.value = true; loadFailed.value = false; failed.value = false; message.value = "";
  try {
    const config = await api.modelConfig();
    const list = Array.isArray(config.providers) && config.providers.length
      ? config.providers
      : [{
        id: "",
        name: config.provider_name || "",
        base_url: config.base_url || "",
        api_format: "openai",
        use_responses_api: false,
        models: [...(config.models || [])],
        api_key_configured: Boolean(config.api_key_configured),
      }];
    providers.value = list.map(provider => ({ ...emptyProvider(), ...provider, api_key: "" }));
    selectedIndex.value = 0;
    catalog.value = []; catalogSource.value = -1; revealed.value = false;
    addingModel.value = false; editingModel.value = -1; modelName.value = "";
    original.value = JSON.stringify(snapshot());
  } catch (error) { loadFailed.value = true; failed.value = true; message.value = error.message; }
  finally { loading.value = false; }
}

function addProvider() {
  providers.value.push(emptyProvider());
  selectedIndex.value = providers.value.length - 1;
  revealed.value = false; message.value = "";
}

function removeProvider(index) {
  providers.value.splice(index, 1);
  selectedIndex.value = Math.max(0, Math.min(selectedIndex.value, providers.value.length - 1));
  if (catalogSource.value === index) { catalog.value = []; catalogSource.value = -1; }
}

function select(index) { selectedIndex.value = index; revealed.value = false; addingModel.value = false; editingModel.value = -1; }

function addModel(value = modelName.value) {
  if (!selected.value) return;
  const model = String(value || "").trim();
  if (model && !selected.value.models.includes(model)) {
    selected.value.models.push(model);
    modelName.value = "";
    addingModel.value = false;
    failed.value = false; message.value = "";
  } else if (model && selected.value.models.includes(model)) {
    failed.value = true; message.value = "模型名称不能重复";
  }
}

function editModel(index) {
  editingModel.value = index;
  editedModelName.value = selected.value?.models[index] || "";
}
function confirmModelEdit(index) {
  const name = editedModelName.value.trim();
  if (!name || selected.value?.models.some((model, position) => position !== index && model === name)) {
    failed.value = true; message.value = name ? "模型名称不能重复" : "模型名称不能为空";
    return;
  }
  failed.value = false; message.value = "";
  selected.value.models[index] = name;
  editingModel.value = -1;
}
function removeModel(index) { selected.value?.models.splice(index, 1); editingModel.value = -1; }

function startProviderDrag(index, event) {
  providerDrag.value = index;
  if (event?.dataTransfer) event.dataTransfer.effectAllowed = "move";
}
function dropProvider(index) {
  const from = providerDrag.value;
  providerDrag.value = -1;
  if (from < 0 || from === index) return;
  const items = [...providers.value];
  const [moved] = items.splice(from, 1);
  items.splice(index, 0, moved);
  providers.value = items;
  selectedIndex.value = index;
}
function startModelDrag(index, event) {
  modelDrag.value = index;
  if (event?.dataTransfer) event.dataTransfer.effectAllowed = "move";
}
function dropModel(index) {
  const from = modelDrag.value;
  modelDrag.value = -1;
  if (!selected.value || from < 0 || from === index) return;
  const models = [...selected.value.models];
  const [moved] = models.splice(from, 1);
  models.splice(index, 0, moved);
  selected.value.models = models;
}

async function perform(kind) {
  if (pending.value) return;
  const provider = selected.value;
  pending.value = kind; message.value = ""; failed.value = false;
  try {
    if (kind === "fetch") {
      const result = await api.fetchModels({
        base_url: provider.base_url,
        api_key: provider.api_key,
        provider_id: provider.id,
        api_format: provider.api_format,
        models: provider.models,
      });
      catalog.value = result.models || [];
      catalogSource.value = selectedIndex.value;
      message.value = `已获取 ${catalog.value.length} 个模型`;
    } else if (kind === "test") {
      await api.testModel({
        base_url: provider.base_url,
        api_key: provider.api_key,
        provider_id: provider.id,
        api_format: provider.api_format,
        use_responses_api: provider.use_responses_api,
        models: provider.models,
      });
      message.value = `连接成功：${provider.name || provider.base_url}`;
    } else {
      await api.saveModelConfig({
        providers: providers.value.map(item => ({
          id: item.id || undefined,
          name: item.name,
          base_url: item.base_url,
          api_key: item.api_key || "",
          api_format: item.api_format,
          use_responses_api: Boolean(item.use_responses_api),
          models: item.models,
        })),
      });
      await load();
      message.value = "模型配置已保存";
      emit("saved");
    }
  } catch (error) { failed.value = true; message.value = error.message; }
  finally { pending.value = ""; }
}
</script>

<template>
  <div class="model-workspace">
    <div class="model-subtabs" role="tablist" aria-label="模型页面">
      <button id="model-config-tab" type="button" role="tab" :aria-selected="activeTab === 'model'" aria-controls="model-config-panel" :tabindex="activeTab === 'model' ? 0 : -1" @keydown.right.prevent="activeTab = 'stats'; $event.currentTarget.nextElementSibling.focus()" @click="activeTab = 'model'">模型</button>
      <button id="model-stats-tab" type="button" role="tab" :aria-selected="activeTab === 'stats'" aria-controls="model-stats-panel" :tabindex="activeTab === 'stats' ? 0 : -1" @keydown.left.prevent="activeTab = 'model'; $event.currentTarget.previousElementSibling.focus()" @click="activeTab = 'stats'">统计</button>
    </div>
    <section id="model-stats-panel" v-if="activeTab === 'stats'" role="tabpanel" aria-labelledby="model-stats-tab"><ModelUsage /></section>
    <div id="model-config-panel" v-show="activeTab === 'model'" role="tabpanel" aria-labelledby="model-config-tab">
  <form class="model-configuration" @submit.prevent="perform('save')" :aria-busy="loading || Boolean(pending)">
    <div v-if="loading" class="settings-loading" role="status">
      <LoaderCircle class="spinning" :size="18" />正在读取模型配置
    </div>
    <div v-else-if="loadFailed" class="settings-load-error" role="alert">
      {{ message }}
      <button class="button light" type="button" @click="load">重试</button>
    </div>

    <div v-else class="model-sheet">
      <div class="model-layout">
        <aside class="provider-rail">
          <header class="rail-head">
            <div>
              <h4>供应商</h4>
            </div>
            <button class="icon-button" type="button" title="添加供应商" aria-label="添加供应商" :disabled="Boolean(pending)" @click="addProvider">
              <Plus :size="16" />
            </button>
          </header>
          <ul class="provider-list">
            <li
              v-for="(provider, index) in providers"
              :key="provider.id || `new-${index}`"
              :class="{ active: index === selectedIndex, dragging: providerDrag === index }"
              draggable="true"
              @dragstart="startProviderDrag(index, $event)"
              @dragover.prevent
              @drop.prevent="dropProvider(index)"
              @dragend="providerDrag = -1"
              @click="select(index)"
            >
              <GripVertical class="provider-grip" :size="14" aria-hidden="true" />
              <span class="provider-badge" aria-hidden="true">{{ (provider.name || "?").slice(0, 1).toUpperCase() }}</span>
              <span class="provider-text">
                <strong>{{ provider.name || "未命名供应商" }}</strong>
                <span v-if="provider.models.length">{{ provider.models[0] }}<template v-if="provider.models.length > 1"> 等 {{ provider.models.length }} 个</template></span>
                <span v-else>尚未添加模型</span>
              </span>
              <span v-if="index === 0" class="provider-default">默认</span>
            </li>
          </ul>
          <p v-if="!providers.length" class="provider-empty">还没有供应商。点击右上角添加。</p>
        </aside>

        <div v-if="selected" class="provider-detail">
          <header class="detail-head">
            <span class="provider-badge large" aria-hidden="true">{{ (selected.name || "?").slice(0, 1).toUpperCase() }}</span>
            <div class="detail-head-copy">
              <h3>{{ selected.name || "新供应商" }}</h3>
              <span v-if="selectedIndex === 0" class="detail-source">默认供应商</span>
            </div>
            <button v-if="providers.length > 1" class="icon-button danger" type="button" :disabled="Boolean(pending)" title="移除该供应商" aria-label="移除该供应商" @click="removeProvider(selectedIndex)">
              <Trash2 :size="15" />
            </button>
          </header>

          <section class="detail-section">
            <header class="detail-section-head">
              <div class="detail-section-copy">
                <h4>连接</h4>
              </div>
            </header>
            <div class="detail-section-body">
              <label class="field-label settings-field">
                <span>供应商名称</span>
                <input v-model="selected.name" autocomplete="off" placeholder="例如：My API Provider" :disabled="Boolean(pending)">
              </label>

              <label class="field-label settings-field">
                <span>API 地址</span>
                <input v-model="selected.base_url" type="url" inputmode="url" autocomplete="off" placeholder="https://api.example.com/v1" :disabled="Boolean(pending)">

              </label>

              <label class="field-label settings-field">
                <span>API 密钥</span>
                <span class="secret-field">
                  <input
                    v-model="selected.api_key"
                    aria-label="API 密钥"
                    :type="revealed ? 'text' : 'password'"
                    autocomplete="new-password"
                    :placeholder="selected.api_key_configured ? '已配置，留空保留原密钥' : '输入 API 密钥'"
                    :disabled="Boolean(pending)"
                  >
                  <button class="icon-button" type="button" :aria-label="revealed ? '隐藏密钥' : '显示密钥'" :title="revealed ? '隐藏密钥' : '显示密钥'" @click="revealed = !revealed">
                    <component :is="revealed ? EyeOff : Eye" :size="15" />
                  </button>
                </span>
              </label>

              <div class="field-label settings-field">
                <span>API 格式</span>
                <div class="format-options" role="group" aria-label="API 格式">
                  <button type="button" :class="{ active: selected.api_format === 'openai' }" :aria-pressed="selected.api_format === 'openai'" :disabled="Boolean(pending)" @click="selected.api_format = 'openai'">OpenAI 格式</button>
                  <button type="button" :class="{ active: selected.api_format === 'anthropic' }" :aria-pressed="selected.api_format === 'anthropic'" :disabled="Boolean(pending)" @click="selected.api_format = 'anthropic'; selected.use_responses_api = false">Anthropic 格式</button>
                </div>
              </div>

              <div class="field-label settings-field switch-field">
                <div class="switch-text">
                  <span>使用 Responses API</span>
                  <small>仅在服务商支持 /v1/responses 时开启。</small>
                </div>
                <button
                  class="switch-control"
                  type="button"
                  role="switch"
                  :aria-checked="selected.use_responses_api && selected.api_format === 'openai'"
                  aria-label="使用 Responses API"
                  :disabled="selected.api_format !== 'openai' || Boolean(pending)"
                  :class="{ on: selected.use_responses_api && selected.api_format === 'openai' }"
                  @click="selected.use_responses_api = !selected.use_responses_api"
                >
                  <span class="switch-knob" aria-hidden="true" />
                </button>
              </div>

            </div>
          </section>

          <section class="detail-section">
            <header class="detail-section-head">
              <div class="detail-section-copy">
                <h4>模型优先级（至少添加一个）</h4>
              </div>
            </header>
            <div class="detail-section-body model-priority-body">
              <div class="model-priority-box">
                <ol v-if="selected.models.length" class="priority-list">
                  <li
                    v-for="(model, index) in selected.models"
                    :key="model"
                    :draggable="editingModel === -1"
                    :class="{ dragging: modelDrag === index }"
                    @dragstart="startModelDrag(index, $event)"
                    @dragover.prevent
                    @drop.prevent="dropModel(index)"
                    @dragend="modelDrag = -1"
                  >
                    <GripVertical class="model-grip" :size="14" aria-hidden="true" />
                    <span class="model-tag" :class="{ primary: index === 0 }">{{ index === 0 ? '主模型' : `备用 ${index}` }}</span>
                    <input v-if="editingModel === index" v-model="editedModelName" class="model-edit-input" :aria-label="`编辑 ${model}`" :disabled="Boolean(pending)" @keydown.enter.prevent="confirmModelEdit(index)" @keydown.esc.prevent="editingModel = -1">
                    <strong v-else class="model-id" :title="model">{{ model }}</strong>
                    <button class="icon-button" type="button" :disabled="Boolean(pending)" :aria-label="editingModel === index ? `确认编辑 ${model}` : `编辑 ${model}`" :title="editingModel === index ? '确认' : '编辑'" @click="editingModel === index ? confirmModelEdit(index) : editModel(index)">
                      <Check v-if="editingModel === index" :size="15" /><Pencil v-else :size="15" />
                    </button>
                    <button class="icon-button" type="button" :disabled="Boolean(pending)" :aria-label="`移除 ${model}`" title="移除" @click="removeModel(index)"><Trash2 :size="15" /></button>
                  </li>
                </ol>
                <p v-else class="model-empty">还没有模型。请添加模型，或从服务商拉取模型列表。</p>
                <div v-if="addingModel" class="model-add-row">
                  <input v-model="modelName" aria-label="模型名称" placeholder="your-model-name" autocomplete="off" :disabled="Boolean(pending)" @keydown.enter.prevent="addModel()" @keydown.esc.prevent="addingModel = false">
                  <button class="button light" type="button" :disabled="!modelName.trim() || Boolean(pending)" @click="addModel()">添加</button>
                  <button class="button light" type="button" @click="addingModel = false">取消</button>
                </div>
                <button v-else class="model-add-trigger" type="button" :disabled="Boolean(pending)" @click="addingModel = true"><Plus :size="16" />添加模型</button>
              </div>
              <p class="model-drag-hint">拖拽调整优先级</p>
              <div class="model-priority-actions">
                <button class="button light" type="button" :disabled="Boolean(pending) || !fetchReady" :title="fetchReady ? '从该供应商读取可用模型' : '请先填写 API 地址和密钥'" @click="perform('fetch')">
                  <LoaderCircle v-if="pending === 'fetch'" class="spinning" :size="16" /><Download v-else :size="16" />从服务商拉取模型列表
                </button>
                <select v-if="activeCatalog.length" class="model-catalog" aria-label="从已获取模型中选择" :disabled="Boolean(pending)" @change="addModel($event.target.value); $event.target.value = ''">
                  <option value="">从已获取模型中选择</option>
                  <option v-for="model in activeCatalog" :key="model" :value="model">{{ model }}</option>
                </select>
                <button class="button light model-test" type="button" :disabled="Boolean(pending) || !fetchReady || !selected.models.length" @click="perform('test')">
                  <LoaderCircle v-if="pending === 'test'" class="spinning" :size="16" /><Zap v-else :size="16" />测试连接
                </button>
              </div>
            </div>
          </section>

        </div>
      </div>

      <footer class="model-footer">
        <span class="preferences-feedback" :class="{ error: failed }" role="status">
          <span v-if="failed || dirty" class="settings-feedback-dot" :class="{ dirty: dirty && !failed, error: failed }" aria-hidden="true" />
          <Check v-if="message && !failed && !pending" :size="14" />
          {{ message || (dirty ? '有未保存的更改' : '') }}
        </span>
        <button class="button dark" type="submit" :disabled="!valid || !dirty || Boolean(pending)">
          <LoaderCircle v-if="pending === 'save'" class="spinning" :size="14" />
          <Save v-else :size="14" />保存配置
        </button>
      </footer>
    </div>
  </form>
    </div>
  </div>
</template>

<style scoped>
/* finesse · component: model-configuration · register=product · shell=colophon-sheet
 * layout=masthead+facts+sections+footer · SOUL=5 SPECTACLE=1 DENSITY=5 */
.model-workspace { min-width: 0; width: 100%; }
.model-subtabs { display: flex; width: fit-content; gap: 4px; padding: 4px; margin-bottom: 20px; border-radius: var(--ds-radius-xl); background: var(--accent-soft); }
.model-subtabs button { min-width: 84px; border: 0; border-radius: var(--ds-radius-lg); padding: 8px 22px; background: transparent; color: var(--muted); cursor: pointer; font: inherit; font-size: var(--ds-text-base); }
.model-subtabs button[aria-selected="true"] { background: var(--surface); color: var(--ink); box-shadow: var(--ds-elevation-100); }
.model-subtabs button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.model-configuration { width: 100%; }

.model-sheet {
  overflow: hidden;
  border: 1px solid var(--ds-border);
  border-radius: var(--ds-radius-md);
  background: var(--ds-surface-100);
  box-shadow: var(--ds-elevation-100);
}

.model-masthead {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 32px;
  min-height: 148px;
  border-bottom: 1px solid var(--line);
  background: var(--ds-surface-input);
  padding: 26px 32px;
}

.model-brand-lockup { display: flex; align-items: center; gap: 20px; min-width: 0; }

.model-mark {
  display: grid;
  place-items: center;
  width: 76px;
  height: 76px;
  flex: none;
  border: 1px solid var(--ds-border);
  border-radius: var(--ds-radius-lg);
  background: var(--ds-surface-100);
  color: var(--accent);
  font-size: var(--ds-text-3xl);
  line-height: 1;
  font-weight: 500;
}

.model-brand-copy { min-width: 0; }
.model-eyebrow { color: var(--accent); font-size: var(--ds-text-xs); }
.model-brand-copy h3 { margin-top: 4px; color: var(--ink); font-size: var(--ds-text-3xl); line-height: 1.1; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.model-metrics { display: flex; gap: 28px; flex: none; }
.model-metrics > div { display: grid; justify-items: end; gap: 4px; }
.model-metrics span { color: var(--muted); font-size: var(--ds-text-xs); }
.model-metrics strong { color: var(--ink); font-size: var(--ds-text-2xl); line-height: 1.1; font-weight: 500; font-variant-numeric: tabular-nums; }

.model-facts {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1.4fr);
  margin: 0;
  border-bottom: 1px solid var(--line);
}

.model-facts > div { display: grid; gap: 6px; padding: 16px 32px; }
.model-facts > div + div { border-left: 1px solid var(--line); }
.model-facts dt { color: var(--muted); font-size: var(--ds-text-xs); }
.model-facts dd { margin: 0; color: var(--ink); font-size: var(--ds-text-base); line-height: 1.5; }

.model-layout { display: grid; grid-template-columns: 184px minmax(0, 1fr); align-items: stretch; }

.provider-rail {
  display: grid;
  align-content: start;
  gap: 10px;
  border-right: 1px solid var(--line);
  background: var(--ds-surface-100);
  padding: 20px 18px 24px;
}

.rail-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 8px; }
.rail-head h4 { margin: 0; color: var(--ink); font-size: var(--ds-text-base); font-weight: 500; }
.rail-head p { margin: 4px 0 0; color: var(--muted); font-size: var(--ds-text-xs); }

.provider-list { display: grid; gap: 4px; margin: 0; padding: 0; list-style: none; }

.provider-list li {
  display: grid;
  grid-template-columns: 14px 22px minmax(0, 1fr) auto;
  align-items: center;
  gap: 8px;
  border: 1px solid transparent;
  border-radius: var(--ds-radius-sm);
  background: transparent;
  padding: 8px;
  cursor: pointer;
}

.provider-list li.active {
  border-color: var(--ds-border);
  background: var(--surface);
  color: var(--accent);
  background: var(--accent-soft);
}

.provider-list li.dragging { opacity: .55; }
.provider-grip { color: var(--subtle); cursor: grab; }

.provider-badge {
  display: grid;
  place-items: center;
  width: 22px;
  height: 22px;
  border-radius: 6px;
  background: var(--accent);
  color: var(--ds-brand-primary-text);
  font-size: var(--ds-text-xs);
  font-weight: 600;
}

.provider-badge.large { width: 34px; height: 34px; border-radius: var(--ds-radius-sm); font-size: var(--ds-text-base); }
.provider-text { display: grid; min-width: 0; }
.provider-text strong { overflow: hidden; font-size: var(--ds-text-base); font-weight: 500; text-overflow: ellipsis; white-space: nowrap; }
.provider-text span { color: var(--muted); font-size: var(--ds-text-xs); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.provider-default {
  border-radius: 4px;
  background: color-mix(in srgb, var(--ds-success) 12%, transparent);
  color: var(--green);
  font-size: var(--ds-text-2xs);
  padding: 1px 4px;
}

.provider-empty, .rail-note { margin: 0; color: var(--muted); font-size: var(--ds-text-xs); line-height: 1.6; }
.rail-note { border-top: 1px solid var(--line); padding-top: 8px; }

.provider-detail { display: block; min-width: 0; background: var(--ds-surface-100); }

.detail-head {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 74px;
  border-bottom: 1px solid var(--line);
  padding: 16px 32px;
}

.detail-head-copy { display: grid; justify-items: start; gap: 4px; flex: 1; min-width: 0; }
.detail-head-copy h3 { margin: 0; font-size: var(--ds-text-lg); line-height: 1.3; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.detail-source { color: var(--muted); font-size: var(--ds-text-xs); }

.detail-section { border-bottom: 1px solid var(--line); }
.detail-section:last-child { border-bottom: 0; }

.detail-section-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding: 18px 24px 0;
}

.detail-section-copy { min-width: 0; flex: 1; }
.detail-section-copy h4 { margin: 0; color: var(--ink); font-size: var(--ds-text-base); font-weight: 500; }
.detail-section-copy p { margin: 4px 0 0; max-width: 620px; color: var(--muted); font-size: var(--ds-text-sm); line-height: 1.55; }

.detail-section-body { display: grid; gap: 16px; padding: 12px 24px 24px; }

.field-label small, .switch-text small {
  display: block;
  margin-top: 4px;
  color: var(--muted);
  font-size: var(--ds-text-xs);
  line-height: 1.6;
}

.format-options {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  min-height: 44px;
  border: 1px solid var(--line);
  border-radius: var(--radius-pill);
  background: var(--bg);
  padding: 4px;
  gap: 2px;
}

.format-options button { border: 0; border-radius: var(--radius-pill); background: transparent; color: var(--muted); font-size: var(--ds-text-base); }
.format-options button.active { background: var(--accent-soft); color: var(--ink); box-shadow: none; }

.switch-field { display: flex; align-items: center; justify-content: space-between; gap: 14px; }
.switch-text { display: grid; min-width: 0; }
.switch-text span { font-size: var(--ds-text-base); }

.switch-control {
  flex: 0 0 auto;
  width: 40px;
  height: 22px;
  border: 0;
  border-radius: var(--ds-radius-pill);
  background: color-mix(in srgb, var(--ds-text-primary) 18%, transparent);
  padding: 2px;
  transition: background 140ms var(--ease);
}

.switch-control .switch-knob {
  display: block;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: var(--ds-surface-100);
  box-shadow: var(--ds-elevation-200);
  transition: transform 140ms var(--ease);
}

.switch-control.on { background: var(--accent); }
.switch-control.on .switch-knob { transform: translateX(18px); }
.switch-control:disabled { opacity: .45; }

.model-priority-body { gap: 0; }
.model-priority-box { min-width: 0; border: 1px solid var(--line); border-radius: var(--radius-card); background: var(--bg); padding: 8px 12px 14px; }
.priority-list { display: grid; gap: 0; margin: 0; padding: 0; list-style: none; }
.priority-list li {
  display: grid;
  grid-template-columns: 16px auto minmax(0, 1fr) 28px 28px;
  align-items: center;
  gap: 8px;
  min-width: 0;
  min-height: 54px;
  border-radius: var(--ds-radius-md);
  padding: 4px 6px;
}
.priority-list li:hover { background: var(--surface-muted); }
.priority-list li.dragging { opacity: .55; }
.model-grip { color: var(--muted); cursor: grab; }
.model-tag { border-radius: var(--radius-pill); background: var(--surface-muted); color: var(--muted); font-size: var(--ds-text-sm); padding: 2px 8px; white-space: nowrap; }
.model-tag.primary { background: var(--ink); color: var(--on-accent); }
.model-id { min-width: 0; overflow: hidden; font-size: var(--ds-text-base); font-weight: 400; text-overflow: ellipsis; white-space: nowrap; }
.model-edit-input { min-width: 0; width: 100%; height: 32px; border: 1px solid var(--line); border-radius: 6px; padding: 0 8px; background: var(--ds-surface-100); }
.priority-list .icon-button { width: 28px; height: 28px; min-width: 28px; color: var(--muted); }
.model-empty { margin: 0; padding: 14px 8px; color: var(--muted); font-size: var(--ds-text-sm); }
.model-add-trigger { display: inline-flex; align-items: center; gap: 10px; min-height: 38px; border: 0; border-radius: var(--ds-radius-sm); background: transparent; padding: 0 8px; color: var(--ink); font-size: var(--ds-text-base); cursor: pointer; }
.model-add-trigger:hover { background: var(--ds-on-surface-active); }
.model-add-row { display: grid; grid-template-columns: minmax(0, 1fr) auto auto; gap: 8px; margin-top: 8px; }
.model-add-row input { width:100%; min-width:0; height:38px; border:1px solid var(--line); border-radius: var(--ds-radius-sm); padding:0 12px; color:var(--ink); background:var(--surface); }
.model-add-row input:focus-visible,.model-edit-input:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
.model-drag-hint { margin: 10px 16px 12px; color: var(--muted); font-size: var(--ds-text-sm); }
.model-priority-actions { display: grid; justify-items: start; gap: 16px; }
.model-priority-actions > .button:first-child { border: 1px solid var(--line-strong); border-radius: var(--radius-pill); background: var(--surface); color: var(--ink); }
.model-priority-actions > .button:first-child:hover:not(:disabled) { background: var(--bg); border-color: var(--ink); }
.model-catalog { min-width: 180px; max-width: 100%; }
.model-priority-actions .model-test { justify-content: center; width: 100%; min-height: 44px; border: 1px solid var(--ink); border-radius: var(--radius-pill); background: var(--surface); color: var(--ink); }
@media (max-width: 620px) { .model-add-row { grid-template-columns: minmax(0, 1fr) auto; } .model-add-row .button:last-child { grid-column: 1/-1; } }

.reasoning-options {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  min-height: 38px;
  border: 1px solid var(--line);
  border-radius: var(--ds-radius-sm);
  background: var(--ds-surface-muted);
  padding: 4px;
  gap: 2px;
}

.reasoning-options button { min-width: 0; border: 0; border-radius: 6px; background: transparent; color: var(--muted); font-size: var(--ds-text-sm); }
.reasoning-options button.active { background: var(--surface); color: var(--accent); box-shadow: inset 0 0 0 1px rgba(var(--accent-rgb),.28); }

.detail-actions { display: flex; flex-wrap: wrap; gap: 8px; }

.model-footer {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
  min-height: 60px;
  border-top: 1px solid var(--line);
  background: var(--surface);
  padding: 12px 32px;
}

.model-footer .preferences-feedback { flex: 1; min-width: 0; color: var(--muted); font-size: var(--ds-text-sm); }

@media (hover: hover) {
  .provider-list li:not(.active):hover { background: var(--ds-on-surface); }
}

@media (max-width: 900px) {
  .model-masthead { align-items: flex-start; padding: 22px 24px; }
  .model-metrics > div { justify-items: start; }
  .model-facts > div { padding: 14px 24px; }
  .model-layout { grid-template-columns: 1fr; }
  .provider-rail { border-right: 0; border-bottom: 1px solid var(--line); padding: 16px 24px 18px; }
  .detail-head { padding-inline: 24px; }
  .detail-section-head { padding-inline: 24px; }
  .detail-section-body { padding-inline: 24px; }
  .model-footer { padding-inline: 24px; }
  .reasoning-options { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .reasoning-options button { min-height: 36px; }
}
</style>
