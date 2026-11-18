<script setup>
import { computed, onMounted, ref } from "vue";
import { Check, Download, Eye, EyeOff, GripVertical, LoaderCircle, Plus, Save, Trash2, Zap } from "@lucide/vue";
import { api } from "../../../app/api.js";

const emit = defineEmits(["saved"]);
const reasoningOptions = [
  { value: "none", label: "无" },
  { value: "minimal", label: "最少" },
  { value: "low", label: "低" },
  { value: "medium", label: "中" },
  { value: "high", label: "高" },
  { value: "xhigh", label: "极高" },
];

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
const reasoningEffort = ref("high");
const selectedIndex = ref(0);
const original = ref("");
const catalog = ref([]);
const catalogSource = ref(-1);
const modelName = ref("");
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
const totalModels = computed(() => providers.value.reduce((sum, item) => sum + item.models.length, 0));
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
    reasoning_effort: reasoningEffort.value,
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
    reasoningEffort.value = config.reasoning_effort || "high";
    selectedIndex.value = 0;
    catalog.value = []; catalogSource.value = -1; revealed.value = false;
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

function select(index) { selectedIndex.value = index; revealed.value = false; }

function addModel(value = modelName.value) {
  if (!selected.value) return;
  const model = String(value || "").trim();
  if (model && !selected.value.models.includes(model)) {
    selected.value.models.push(model);
    modelName.value = "";
  }
}

function removeModel(index) { selected.value?.models.splice(index, 1); }

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
        reasoning_effort: reasoningEffort.value,
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
  <form class="model-configuration" @submit.prevent="perform('save')" :aria-busy="loading || Boolean(pending)">
    <div v-if="loading" class="settings-loading" role="status">
      <LoaderCircle class="spinning" :size="18" />正在读取模型配置
    </div>
    <div v-else-if="loadFailed" class="settings-load-error" role="alert">
      {{ message }}
      <button class="button light" type="button" @click="load">重试</button>
    </div>

    <div v-else class="model-sheet">
      <header class="model-masthead">
        <div class="model-brand-lockup">
          <span class="model-mark" aria-hidden="true">{{ (selected?.name || "?").slice(0, 1).toUpperCase() }}</span>
          <div class="model-brand-copy">
            <span class="model-eyebrow">模型服务</span>
            <h3>{{ selected?.name || "模型配置" }}</h3>
          </div>
        </div>
        <div class="model-metrics">
          <div><span>供应商</span><strong>{{ providers.length }}</strong></div>
          <div><span>模型</span><strong>{{ totalModels }}</strong></div>
        </div>
      </header>

      <dl class="model-facts">
        <div>
          <dt>默认供应商</dt>
          <dd>{{ providers[0]?.name || "未配置" }}</dd>
        </div>
        <div>
          <dt>回退顺序</dt>
          <dd>供应商内按模型顺序，全部不可用再换下一个供应商</dd>
        </div>
      </dl>

      <div class="model-layout">
        <aside class="provider-rail">
          <header class="rail-head">
            <div>
              <h4>供应商</h4>
              <p>拖拽排序，首位为默认</p>
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
          <p class="rail-note">该供应商的模型全部不可用时，才按顺序换用下一个。</p>
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
                <p>填写服务商文档里的地址与密钥，再拉取可用模型。</p>
              </div>
            </header>
            <div class="detail-section-body">
              <label class="field-label settings-field">
                <span>供应商名称</span>
                <input v-model="selected.name" autocomplete="off" placeholder="例如：My API Provider" :disabled="Boolean(pending)">
              </label>

              <label class="field-label settings-field">
                <span>API 地址（自定义服务）</span>
                <input v-model="selected.base_url" type="url" inputmode="url" autocomplete="off" placeholder="https://api.example.com/v1" :disabled="Boolean(pending)">
                <small>填写服务商文档里的 Base URL，结尾是否带 /v1 按服务商说明。</small>
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
                  <small>改为调用 /v1/responses。仅当中转或服务商支持该端点时开启，否则会返回 404。</small>
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

              <div class="detail-actions">
                <button class="button light" type="button" :disabled="Boolean(pending) || !fetchReady" :title="fetchReady ? '从该供应商读取可用模型' : '请先填写 API 地址和密钥'" @click="perform('fetch')">
                  <LoaderCircle v-if="pending === 'fetch'" class="spinning" :size="14" />
                  <Download v-else :size="14" />拉取模型列表
                </button>
                <button class="button light" type="button" :disabled="Boolean(pending) || !fetchReady" @click="perform('test')">
                  <LoaderCircle v-if="pending === 'test'" class="spinning" :size="14" />
                  <Zap v-else :size="14" />测试连接
                </button>
              </div>
            </div>
          </section>

          <section class="detail-section">
            <header class="detail-section-head">
              <div class="detail-section-copy">
                <h4>模型优先级</h4>
                <p>同一供应商内按此顺序尝试，主模型失败后依次回退。</p>
              </div>
              <span class="settings-scope">{{ selected.models.length }} 个模型</span>
            </header>
            <div class="detail-section-body">
              <ol v-if="selected.models.length" class="priority-list">
                <li
                  v-for="(model, index) in selected.models"
                  :key="model"
                  draggable="true"
                  :class="{ dragging: modelDrag === index }"
                  @dragstart="startModelDrag(index, $event)"
                  @dragover.prevent
                  @drop.prevent="dropModel(index)"
                  @dragend="modelDrag = -1"
                >
                  <GripVertical class="model-grip" :size="14" aria-hidden="true" />
                  <span class="model-tag" :class="{ primary: index === 0 }">{{ index === 0 ? '主模型' : `备用 ${index}` }}</span>
                  <strong class="model-id">{{ model }}</strong>
                  <button class="icon-button" type="button" :disabled="Boolean(pending)" :aria-label="`移除 ${model}`" title="移除" @click="removeModel(index)">
                    <Trash2 :size="14" />
                  </button>
                </li>
              </ol>
              <p v-else class="model-empty">还没有模型。连接服务后获取模型，或手动填写模型名称。</p>
              <div class="model-add-row">
                <input v-model="modelName" aria-label="模型名称" placeholder="your-model-name" autocomplete="off" :disabled="Boolean(pending)" @keydown.enter.prevent="addModel()">
                <button class="button light" type="button" :disabled="!modelName.trim() || Boolean(pending)" @click="addModel()"><Plus :size="14" />添加模型</button>
              </div>
              <select v-if="activeCatalog.length" class="model-catalog" aria-label="从已获取模型中选择" :disabled="Boolean(pending)" @change="addModel($event.target.value); $event.target.value = ''">
                <option value="">从已获取模型中选择</option>
                <option v-for="model in activeCatalog" :key="model" :value="model">{{ model }}</option>
              </select>
            </div>
          </section>

          <section class="detail-section">
            <header class="detail-section-head">
              <div class="detail-section-copy">
                <h4>思考强度</h4>
                <p>对全部供应商生效，随每次模型请求一起提交。</p>
              </div>
            </header>
            <div class="detail-section-body">
              <div class="reasoning-options" role="group" aria-label="思考强度">
                <button
                  v-for="option in reasoningOptions"
                  :key="option.value"
                  type="button"
                  :class="{ active: reasoningEffort === option.value }"
                  :aria-pressed="reasoningEffort === option.value"
                  :disabled="Boolean(pending)"
                  @click="reasoningEffort = option.value"
                >
                  {{ option.label }}
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
</template>

<style scoped>
/* finesse · component: model-configuration · register=product · shell=colophon-sheet
 * layout=masthead+facts+sections+footer · SOUL=5 SPECTACLE=1 DENSITY=5 */
.model-configuration { width: 100%; }

.model-sheet {
  overflow: hidden;
  border: 1px solid rgba(24, 33, 44, .11);
  border-radius: 14px;
  background: #fdfefe;
  box-shadow: 0 1px 3px rgba(20, 30, 42, .04);
}

.model-masthead {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 32px;
  min-height: 148px;
  border-bottom: 1px solid var(--line);
  background: #f7f9fb;
  padding: 26px 32px;
}

.model-brand-lockup { display: flex; align-items: center; gap: 20px; min-width: 0; }

.model-mark {
  display: grid;
  place-items: center;
  width: 76px;
  height: 76px;
  flex: none;
  border: 1px solid rgba(24, 33, 44, .10);
  border-radius: 18px;
  background: #fdfefe;
  color: var(--accent);
  font-size: 26px;
  line-height: 1;
  font-weight: 500;
}

.model-brand-copy { min-width: 0; }
.model-eyebrow { color: var(--accent); font-size: 11px; }
.model-brand-copy h3 { margin-top: 3px; color: var(--ink); font-size: 28px; line-height: 1.1; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.model-metrics { display: flex; gap: 28px; flex: none; }
.model-metrics > div { display: grid; justify-items: end; gap: 3px; }
.model-metrics span { color: var(--muted); font-size: 11px; }
.model-metrics strong { color: var(--ink); font-size: 24px; line-height: 1.1; font-weight: 500; font-variant-numeric: tabular-nums; }

.model-facts {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1.4fr);
  margin: 0;
  border-bottom: 1px solid var(--line);
}

.model-facts > div { display: grid; gap: 6px; padding: 16px 32px; }
.model-facts > div + div { border-left: 1px solid var(--line); }
.model-facts dt { color: var(--muted); font-size: 11px; }
.model-facts dd { margin: 0; color: var(--ink); font-size: 13px; line-height: 1.5; }

.model-layout { display: grid; grid-template-columns: 248px minmax(0, 1fr); align-items: stretch; }

.provider-rail {
  display: grid;
  align-content: start;
  gap: 10px;
  border-right: 1px solid var(--line);
  background: #fbfcfd;
  padding: 20px 18px 24px;
}

.rail-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 8px; }
.rail-head h4 { margin: 0; color: var(--ink); font-size: 14px; font-weight: 500; }
.rail-head p { margin: 3px 0 0; color: var(--muted); font-size: 11px; }

.provider-list { display: grid; gap: 4px; margin: 0; padding: 0; list-style: none; }

.provider-list li {
  display: grid;
  grid-template-columns: 14px 22px minmax(0, 1fr) auto;
  align-items: center;
  gap: 8px;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  padding: 8px;
  cursor: pointer;
}

.provider-list li.active {
  border-color: rgba(24, 33, 44, .13);
  background: var(--surface);
  box-shadow: inset 2px 0 0 var(--accent), 0 1px 2px rgba(20, 30, 42, .05);
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
  color: #fff;
  font-size: 11px;
  font-weight: 600;
}

.provider-badge.large { width: 34px; height: 34px; border-radius: 8px; font-size: 14px; }
.provider-text { display: grid; min-width: 0; }
.provider-text strong { overflow: hidden; font-size: 13px; font-weight: 500; text-overflow: ellipsis; white-space: nowrap; }
.provider-text span { color: var(--muted); font-size: 11px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.provider-default {
  border-radius: 4px;
  background: rgba(22, 137, 91, .12);
  color: var(--green);
  font-size: 10px;
  padding: 1px 5px;
}

.provider-empty, .rail-note { margin: 0; color: var(--muted); font-size: 11px; line-height: 1.6; }
.rail-note { border-top: 1px solid var(--line); padding-top: 9px; }

.provider-detail { display: block; min-width: 0; background: #fdfefe; }

.detail-head {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 74px;
  border-bottom: 1px solid var(--line);
  padding: 16px 32px;
}

.detail-head-copy { display: grid; justify-items: start; gap: 4px; flex: 1; min-width: 0; }
.detail-head-copy h3 { margin: 0; font-size: 17px; line-height: 1.3; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.detail-source { color: var(--muted); font-size: 11px; }

.detail-section { border-bottom: 1px solid var(--line); }
.detail-section:last-child { border-bottom: 0; }

.detail-section-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding: 20px 32px 2px;
}

.detail-section-copy { min-width: 0; flex: 1; }
.detail-section-copy h4 { margin: 0; color: var(--ink); font-size: 14px; font-weight: 500; }
.detail-section-copy p { margin: 4px 0 0; max-width: 620px; color: var(--muted); font-size: 12px; line-height: 1.55; }

.detail-section-body { display: grid; gap: 15px; padding: 14px 32px 24px; }

.field-label small, .switch-text small {
  display: block;
  margin-top: 4px;
  color: var(--muted);
  font-size: 11px;
  line-height: 1.6;
}

.format-options {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  min-height: 38px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #f3f5f7;
  padding: 3px;
  gap: 2px;
}

.format-options button { border: 0; border-radius: 5px; background: transparent; color: var(--muted); font-size: 12px; }
.format-options button.active { background: var(--surface); color: var(--accent); box-shadow: inset 0 0 0 1px rgba(var(--accent-rgb),.28); }

.switch-field { display: flex; align-items: center; justify-content: space-between; gap: 14px; }
.switch-text { display: grid; min-width: 0; }
.switch-text span { font-size: 13px; }

.switch-control {
  flex: 0 0 auto;
  width: 40px;
  height: 22px;
  border: 0;
  border-radius: 999px;
  background: rgba(24, 33, 44, .18);
  padding: 2px;
  transition: background 140ms var(--ease);
}

.switch-control .switch-knob {
  display: block;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 1px 2px rgba(20, 30, 42, .25);
  transition: transform 140ms var(--ease);
}

.switch-control.on { background: var(--accent); }
.switch-control.on .switch-knob { transform: translateX(18px); }
.switch-control:disabled { opacity: .45; }

.priority-list { display: grid; gap: 4px; margin: 0; padding: 0; list-style: none; }

.priority-list li {
  display: grid;
  grid-template-columns: 14px auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 10px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fafbfc;
  padding: 8px 10px;
}

.priority-list li.dragging { opacity: .55; }
.model-grip { color: var(--subtle); cursor: grab; }

.model-tag {
  border-radius: 4px;
  background: rgba(24, 33, 44, .08);
  color: var(--muted);
  font-size: 10px;
  padding: 2px 6px;
  white-space: nowrap;
}

.model-tag.primary { background: rgba(var(--accent-rgb),.12); color: var(--accent); }
.model-id { overflow: hidden; font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }

.model-empty {
  margin: 0;
  border: 1px dashed rgba(24, 33, 44, .18);
  border-radius: 8px;
  background: #fafbfc;
  padding: 14px;
  color: var(--muted);
  font-size: 12px;
}

.model-add-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px; }
.model-catalog { min-width: 180px; }

.reasoning-options {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  min-height: 38px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #f3f5f7;
  padding: 3px;
  gap: 2px;
}

.reasoning-options button { min-width: 0; border: 0; border-radius: 5px; background: transparent; color: var(--muted); font-size: 12px; }
.reasoning-options button.active { background: var(--surface); color: var(--accent); box-shadow: inset 0 0 0 1px rgba(var(--accent-rgb),.28); }

.detail-actions { display: flex; flex-wrap: wrap; gap: 8px; }

.model-footer {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 60px;
  border-top: 1px solid var(--line);
  background: #fbfcfd;
  padding: 12px 32px;
}

.model-footer .preferences-feedback { flex: 1; min-width: 0; color: var(--muted); font-size: 12px; }

@media (hover: hover) {
  .provider-list li:not(.active):hover { background: #eef2f6; }
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
