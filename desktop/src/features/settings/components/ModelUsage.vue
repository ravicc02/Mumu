<script setup>
import { computed, onMounted, ref, watch } from "vue";
import { ChevronLeft, ChevronRight, LoaderCircle, RefreshCw } from "@lucide/vue";
import { api } from "../../../app/api.js";

const period = ref("24h"), model = ref(""), provider = ref(""), status = ref("all");
const view = ref("requests"), details = ref(false), offset = ref(0), limit = 50;
const data = ref(null), tools = ref(null), pricing = ref([]);
const tool = ref(""), toolStatus = ref("all"), toolOffset = ref(0);
const loading = ref(false), error = ref(""), saving = ref(false), saveError = ref(""), saved = ref("");
const draft = ref({ provider_id: "", model: "", currency: "CNY", input_price: "", output_price: "", cache_read_price: "", cache_write_price: "" });
const priceFields = [{ key: "input_price", label: "普通输入" }, { key: "output_price", label: "输出" }, { key: "cache_read_price", label: "缓存读取" }, { key: "cache_write_price", label: "缓存创建" }];
const views = [{ id: "requests", label: "请求日志" }, { id: "providers", label: "供应商统计" }, { id: "models", label: "模型统计" }, { id: "tools", label: "工具统计" }, { id: "pricing", label: "定价配置" }];
const periods = [{ id: "24h", label: "24h" }, { id: "7d", label: "近 7 天" }, { id: "30d", label: "近 30 天" }, { id: "all", label: "全部" }];
const statuses = { succeeded: "成功", error: "失败", running: "进行中", cancelled: "已取消" };
const summary = computed(() => data.value?.summary);
const groups = computed(() => view.value === "providers" ? data.value?.by_provider || [] : data.value?.by_model || []);
const providers = computed(() => data.value?.providers || []);
const providerName = id => providers.value.find(p => p.id === id)?.name || id;
let sequence = 0;
function number(value) { return value == null ? "—" : new Intl.NumberFormat("zh-CN", { notation: "compact", maximumFractionDigits: 1 }).format(value); }
function percent(value) { return value == null ? "—" : `${(value * 100).toFixed(1)}%`; }
function duration(value) { return value == null ? "—" : `${(value / 1000).toFixed(1)}s`; }
function time(value) { if (!value) return "—"; const date = new Date(value); return Number.isNaN(+date) ? "—" : date.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }); }
function money(value, currency) { return value == null ? "—" : new Intl.NumberFormat("zh-CN", { style: "currency", currency, maximumFractionDigits: 6 }).format(value); }
function costs(values) { return Object.entries(values || {}).map(([currency, value]) => money(value, currency)).join(" / ") || "—"; }
async function load() {
  const request = ++sequence;
  loading.value = true; error.value = "";
  try {
    const result = await api.modelUsage({ period: period.value, model: model.value, provider_id: provider.value, status: status.value, details: details.value, limit, offset: offset.value });
    let extra;
    if (view.value === "tools") extra = await api.toolUsage({ period: period.value, tool: tool.value, status: toolStatus.value, limit, offset: toolOffset.value });
    if (view.value === "pricing") extra = await api.usagePricing();
    if (request !== sequence) return;
    data.value = result;
    if (view.value === "tools") tools.value = extra;
    if (view.value === "pricing") pricing.value = extra.rules;
  } catch (failure) {
    if (request === sequence) error.value = failure.message === "Not Found" ? "当前本地服务尚未加载完整统计接口，请在任务结束后重启 Mumu 后重试。" : failure.message;
  } finally { if (request === sequence) loading.value = false; }
}
function reset() { if (offset.value || toolOffset.value) { offset.value = 0; toolOffset.value = 0; } else load(); }
watch([period, model, provider, status, details, tool, toolStatus], reset);
watch([offset, toolOffset, view], load);
async function savePrice() {
  saving.value = true; saveError.value = ""; saved.value = "";
  try {
    const payload = { ...draft.value, provider_id: draft.value.provider_id.trim(), model: draft.value.model.trim() };
    for (const { key } of priceFields) {
      if (payload[key] === "" || !Number.isFinite(Number(payload[key])) || Number(payload[key]) < 0) throw new Error("请填写四项非负单价；免费项目请明确填写 0。");
      payload[key] = Number(payload[key]);
    }
    if (!payload.provider_id || !payload.model) throw new Error("请选择供应商并填写完整模型名称。");
    await api.saveUsagePricing(payload);
    // 规则已成功写入，即使后续读取失败也不能引导重复提交。
    saved.value = "单价已保存，从现在起生效；历史请求不重新计价。";
    await load();
  } catch (failure) { saveError.value = failure.message; }
  finally { saving.value = false; }
}
onMounted(load);
</script>

<template>
  <div class="model-usage" :aria-busy="loading">
    <header class="usage-toolbar">
      <div class="usage-toolbar-actions">
        <div class="usage-periods" role="group" aria-label="统计时间范围">
          <button v-for="item in periods" :key="item.id" type="button" :aria-pressed="period === item.id" @click="period = item.id">{{ item.label }}</button>
        </div>
        <button class="icon-button" type="button" aria-label="刷新统计" title="刷新统计" :disabled="loading" @click="load"><RefreshCw :size="16" :class="{ spinning: loading }" /></button>
      </div>
    </header>
    <div v-if="error" class="settings-load-error" role="alert">{{ error }}<button class="button light" type="button" :disabled="loading" @click="load">重试</button></div>
    <div v-if="loading" class="settings-loading" role="status"><LoaderCircle :size="18" class="spinning" />正在读取用量</div>
    <template v-if="summary && !error">
      <div class="usage-metrics">
        <section><span>总请求</span><strong>{{ number(summary.requests) }}</strong><small>成功 {{ number(summary.succeeded) }} · 失败 {{ number(summary.errors) }}</small></section>
        <section><span>估算费用</span><strong class="usage-money">{{ costs(summary.costs) }}</strong><small>{{ summary.priced_requests ?? 0 }} / {{ summary.requests }} 条可计价</small></section>
        <section><span>总 Token</span><strong>{{ summary.usage_requests ? number(summary.total_tokens) : '—' }}</strong><small>输入 {{ number(summary.input_tokens) }} / 输出 {{ number(summary.output_tokens) }}</small></section>
        <section><span>缓存命中率</span><strong>{{ percent(summary.cache_hit_rate) }}</strong><small>按请求 {{ percent(summary.cache_request_hit_rate) }} · 命中 {{ number(summary.cached_tokens) }} / 创建 {{ number(summary.cache_creation_tokens) }}</small></section>
      </div>
    </template>
    <section class="usage-query-panel" aria-label="用量查询">
    <div class="usage-tabs" role="tablist" aria-label="统计分类">
      <button v-for="item in views" :id="`usage-tab-${item.id}`" :key="item.id" type="button" role="tab" :aria-selected="view === item.id" aria-controls="usage-content" :tabindex="view === item.id ? 0 : -1" @click="view = item.id" @keydown.right.prevent="view = views[(views.findIndex(v => v.id === view) + 1) % views.length].id; $event.currentTarget.parentElement.querySelector(`[id=usage-tab-${view}]`).focus()" @keydown.left.prevent="view = views[(views.findIndex(v => v.id === view) + views.length - 1) % views.length].id; $event.currentTarget.parentElement.querySelector(`[id=usage-tab-${view}]`).focus()">{{ item.label }}</button>
    </div>
    <div id="usage-content" role="tabpanel" :aria-labelledby="`usage-tab-${view}`">
      <template v-if="view !== 'tools' && view !== 'pricing'">
        <div class="usage-filters">
          <select v-model="provider" aria-label="筛选供应商"><option value="">全部供应商</option><option v-for="item in providers" :key="item.id" :value="item.id">{{ item.name }}</option></select>
          <select v-model="model" aria-label="筛选模型"><option value="">全部模型</option><option v-if="model && !data?.models?.includes(model)" :value="model">{{ model }}</option><option v-for="item in data?.models || []" :key="item" :value="item">{{ item }}</option></select>
          <select v-model="status" aria-label="筛选请求状态"><option value="all">全部状态</option><option v-for="(label, id) in statuses" :key="id" :value="id">{{ label }}</option></select>
          <div class="usage-filter-meta"><label v-if="view === 'requests'" class="usage-detail-toggle"><input v-model="details" type="checkbox">详细记录</label><span v-if="data" class="usage-count">共 {{ data.total.toLocaleString() }} 条记录</span></div>
        </div>
        <template v-if="data && !error && !loading">
          <div v-if="view === 'requests'" class="usage-table-wrap">
            <table class="usage-table usage-requests">
              <colgroup><col class="usage-col-time"><col class="usage-col-provider"><col class="usage-col-model"><col class="usage-col-token"><col class="usage-col-cost"><col class="usage-col-latency"><col class="usage-col-status"></colgroup>
              <thead><tr><th>时间</th><th>供应商</th><th>模型</th><th class="numeric">Token</th><th class="numeric">估算费用</th><th class="numeric">耗时</th><th>状态</th></tr></thead>
              <tbody>
                <template v-for="record in data.records" :key="record.id">
                  <tr>
                    <td :title="record.created_at">{{ time(record.created_at) }}</td>
                    <td><span class="usage-truncate" :title="record.provider_name">{{ record.provider_name }}</span></td>
                    <td><span class="usage-truncate" :title="record.model">{{ record.model }}</span></td>
                    <td class="numeric" :title="`输入 ${record.input_tokens ?? '未返回'} / 输出 ${record.output_tokens ?? '未返回'} / 缓存命中 ${record.cached_tokens ?? '未返回'}`">{{ record.total_tokens == null ? '未返回' : number(record.total_tokens) }}</td>
                    <td class="numeric" :title="record.cost_reason || '按调用时单价估算'">{{ money(record.estimated_cost, record.currency) }}</td>
                    <td class="numeric">{{ duration(record.latency_ms) }}</td>
                    <td><span class="usage-status" :class="record.status">{{ statuses[record.status] || record.status }}</span><small>{{ record.http_status ?? 'HTTP 未记录' }}</small></td>
                  </tr>
                  <tr v-if="details" class="usage-detail-row"><td colspan="7"><details><summary>调用详情 · {{ record.id }}</summary><dl><dt>关联记录</dt><dd>{{ record.details?.attempt_id || '未记录' }}</dd><dt>角色 / 候选序号</dt><dd>{{ record.details?.role || '未记录' }} / {{ record.details?.candidate_index ?? '—' }}</dd><dt>输入 / 输出</dt><dd>{{ record.input_tokens ?? '未返回' }} / {{ record.output_tokens ?? '未返回' }}</dd><dt>缓存命中 / 创建</dt><dd>{{ record.cached_tokens ?? '未返回' }} / {{ record.cache_creation_tokens ?? '未返回' }}</dd><dt>结束时间</dt><dd>{{ time(record.details?.finished_at) }}</dd><dt>计价说明</dt><dd>{{ record.cost_reason || '使用调用发生时的单价版本估算' }}</dd></dl><p>仅显示安全元数据，不记录请求正文、响应正文或密钥。</p></details></td></tr>
                </template>
                <tr v-if="!data.records.length"><td colspan="7" class="usage-empty">{{ model || provider || status !== 'all' ? '没有符合筛选条件的调用' : '该时间范围内暂无模型调用' }}</td></tr>
              </tbody>
            </table>
          </div>
          <div v-else class="usage-table-wrap">
            <table class="usage-table"><thead><tr><th>{{ view === 'providers' ? '供应商' : '模型 / 供应商' }}</th><th class="numeric">请求</th><th class="numeric">成功 / 失败</th><th class="numeric">Token</th><th class="numeric">估算费用</th><th class="numeric">缓存命中率</th></tr></thead><tbody>
              <tr v-for="group in groups" :key="`${group.provider_id}-${group.model || ''}`"><td><span class="usage-truncate" :title="group.model || group.provider_name">{{ group.model || group.provider_name }}</span><small v-if="group.model">{{ group.provider_name }}</small></td><td class="numeric">{{ number(group.requests) }}</td><td class="numeric">{{ number(group.succeeded) }} / {{ number(group.errors) }}</td><td class="numeric">{{ number(group.total_tokens) }}</td><td class="numeric">{{ costs(group.costs) }}<small>{{ group.priced_requests }} 条可计价</small></td><td class="numeric">{{ percent(group.cache_hit_rate) }}<small>按请求 {{ percent(group.cache_request_hit_rate) }}</small></td></tr>
              <tr v-if="!groups.length"><td colspan="6" class="usage-empty">没有符合筛选条件的统计记录</td></tr>
            </tbody></table>
          </div>
          <footer v-if="view === 'requests' && data.total > limit" class="usage-pagination"><span>{{ offset + 1 }}–{{ Math.min(offset + limit, data.total) }} / {{ data.total }}</span><button class="icon-button" type="button" aria-label="上一页" :disabled="!offset" @click="offset -= limit"><ChevronLeft :size="16" /></button><button class="icon-button" type="button" aria-label="下一页" :disabled="offset + limit >= data.total" @click="offset += limit"><ChevronRight :size="16" /></button></footer>
        </template>
      </template>
      <template v-else-if="view === 'tools'">
        <p class="usage-note">独立统计已接入执行台账的业务工具，不代表全部模型工具调用，不按模型或供应商筛选。成功指工具返回成功，不代表业务任务最终完成；幂等重放不重复计数。旧的“进行中”记录可能来自已退出的进程。</p>
        <div class="usage-filters"><select v-model="tool" aria-label="筛选工具"><option value="">全部工具</option><option v-for="name in tools?.tools || []" :key="name">{{ name }}</option></select><select v-model="toolStatus" aria-label="筛选工具状态"><option value="all">全部状态</option><option v-for="id in ['succeeded', 'error', 'running']" :key="id" :value="id">{{ statuses[id] }}</option></select><span class="usage-count">共 {{ tools?.total ?? 0 }} 条执行记录</span></div>
        <template v-if="tools && !loading && !error">
          <div class="usage-table-wrap"><table class="usage-table"><thead><tr><th>工具</th><th class="numeric">执行次数</th><th class="numeric">成功 / 失败</th><th class="numeric">进行中记录</th><th class="numeric">平均耗时</th></tr></thead><tbody><tr v-for="group in tools.groups" :key="group.tool"><td class="usage-wrap">{{ group.tool }}</td><td class="numeric">{{ group.requests }}</td><td class="numeric">{{ group.succeeded }} / {{ group.errors }}</td><td class="numeric">{{ group.running }}</td><td class="numeric">{{ duration(group.average_latency_ms) }}</td></tr><tr v-if="!tools.groups.length"><td colspan="5" class="usage-empty">暂无已接入台账的工具执行记录；后续执行会在这里显示。</td></tr></tbody></table></div>
          <h4>执行记录</h4>
          <div class="usage-table-wrap"><table class="usage-table"><thead><tr><th>时间</th><th>工具</th><th>状态</th><th class="numeric">耗时</th><th>渠道 / 异常类型</th></tr></thead><tbody><tr v-for="record in tools.records" :key="record.id"><td>{{ time(record.created_at) }}</td><td><details><summary class="usage-wrap">{{ record.tool }}</summary><p class="usage-wrap">执行 ID：{{ record.id }}<br>关联记录：{{ record.attempt_id || '未记录' }}</p></details></td><td>{{ statuses[record.status] || record.status }}</td><td class="numeric">{{ duration(record.latency_ms) }}</td><td class="usage-wrap">{{ record.channel || '未记录渠道' }}<small>{{ record.error_code || '—' }}</small></td></tr><tr v-if="!tools.records.length"><td colspan="5" class="usage-empty">没有符合筛选条件的执行记录</td></tr></tbody></table></div>
          <footer v-if="tools.total > limit" class="usage-pagination"><span>{{ toolOffset + 1 }}–{{ Math.min(toolOffset + limit, tools.total) }} / {{ tools.total }}</span><button class="icon-button" type="button" aria-label="工具上一页" :disabled="!toolOffset" @click="toolOffset -= limit"><ChevronLeft :size="16" /></button><button class="icon-button" type="button" aria-label="工具下一页" :disabled="toolOffset + limit >= tools.total" @click="toolOffset += limit"><ChevronRight :size="16" /></button></footer>
        </template>
      </template>
      <template v-else>
        <p class="usage-note">每百万 Token 单价。规则按“供应商 + 完整模型名称”匹配，从保存时起生效，重复保存会新增版本，不覆盖历史价格。不自动换汇，不包含折扣、订阅和其他平台费用。未配置时不估算。</p>
        <form class="usage-pricing-form" @submit.prevent="savePrice">
          <label>供应商<select v-model="draft.provider_id" aria-label="供应商" required :disabled="saving"><option value="" disabled>选择供应商</option><option v-for="item in providers" :key="item.id" :value="item.id">{{ item.name }}</option></select></label>
          <label>完整模型名称<input v-model="draft.model" required maxlength="200" placeholder="与调用记录中的名称一致" list="usage-model-names" :disabled="saving"><datalist id="usage-model-names"><option v-for="name in data?.models || []" :key="name" :value="name" /></datalist></label>
          <label>币种<select v-model="draft.currency" :disabled="saving"><option value="CNY">CNY · 人民币</option><option value="USD">USD · 美元</option></select></label>
          <label v-for="field in priceFields" :key="field.key">{{ field.label }}<input v-model="draft[field.key]" type="number" min="0" max="1000000" step="any" required placeholder="0.00" :disabled="saving"></label>
          <div class="usage-pricing-actions"><button class="button" type="submit" :disabled="saving || loading">{{ saving ? '正在保存…' : '保存新单价' }}</button><span>新价格不追溯历史请求</span></div>
        </form>
        <p v-if="saveError" role="alert" class="usage-save-error">{{ saveError }}</p><p v-if="saved" role="status">{{ saved }}</p>
        <h4>单价版本</h4>
        <div v-if="!loading && !error" class="usage-table-wrap"><table class="usage-table"><thead><tr><th>模型 / 供应商</th><th>生效时间 / 币种</th><th class="numeric">输入 / 输出</th><th class="numeric">缓存读取 / 创建</th></tr></thead><tbody><tr v-for="rule in [...pricing].reverse()" :key="rule.id"><td class="usage-wrap">{{ rule.model }}<small>{{ providerName(rule.provider_id) }}</small></td><td :title="rule.effective_at">{{ time(rule.effective_at) }}<small>{{ rule.currency }}</small></td><td class="numeric">{{ rule.input_price }} / {{ rule.output_price }}</td><td class="numeric">{{ rule.cache_read_price }} / {{ rule.cache_write_price }}</td></tr><tr v-if="!pricing.length"><td colspan="4" class="usage-empty">尚未配置单价。填写供应商实际价格后，后续请求将显示估算费用。</td></tr></tbody></table></div>
      </template>
    </div>
    </section>
  </div>
</template>

<style scoped>
.model-usage { min-width: 0; color: var(--ink); }
.usage-toolbar, .usage-toolbar-actions, .usage-filters, .usage-pagination, .usage-secondary { display: flex; align-items: center; gap: 12px; }
.usage-toolbar { justify-content: flex-end; margin-bottom: 24px; flex-wrap: wrap; }
.usage-toolbar h3 { margin: 0; font-size: 16px; font-weight: 600; }
.usage-periods { display: flex; padding: 3px; border-radius: 22px; background: var(--accent-soft, #edf3f2); }
.usage-periods button { border: 0; border-radius: 20px; background: transparent; color: var(--muted); padding: 7px 13px; font: inherit; font-size: 12px; cursor: pointer; white-space: nowrap; }
.usage-periods button[aria-pressed="true"] { color: var(--ink); background: var(--surface, #fff); box-shadow: 0 1px 3px #152b2518; }
button:focus-visible, summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.usage-metrics { display: grid; grid-template-columns: repeat(4,minmax(0,1fr)); gap: 12px; }
.usage-metrics section { display: grid; grid-template-rows: 20px 34px auto; align-content: start; gap: 8px; min-width: 0; min-height: 150px; padding: 18px; border: 1px solid var(--line); border-radius: var(--radius-card); background: var(--surface); }
.usage-metrics span { color: var(--muted); font-size: 12px; }
.usage-metrics strong { font-size: 26px; line-height: 1.2; font-weight: 600; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.usage-metrics .usage-money { font-size: 22px; }
.usage-metrics small, .usage-table small { display: block; color: var(--muted); font-size: 11px; line-height: 1.6; }
.usage-secondary { flex-wrap: wrap; margin-top: 14px; color: var(--muted); font-size: 12px; gap: 8px 24px; }
.usage-filters { display: grid; grid-template-columns: minmax(110px,1fr) minmax(140px,1.4fr) 112px auto; margin: 0 0 12px; gap: 12px; }
.usage-filter-meta { display: flex; align-items: center; justify-content: flex-end; gap: 16px; min-height: 40px; white-space: nowrap; }
.usage-detail-toggle input { margin: 0; accent-color: var(--accent); }
.usage-filters select { width: 100%; min-width: 0; max-width: none; height: 40px; text-overflow: ellipsis; }
.model-usage { container-type: inline-size; }
@container (max-width: 720px) {
  .usage-filters { grid-template-columns: minmax(0,1fr) minmax(0,1.4fr) 112px; }
  .usage-filter-meta { grid-column: 1 / -1; }
}
.model-usage select, .usage-pricing-form input { min-height: 36px; border: 1px solid var(--line); border-radius: 8px; padding: 6px 10px; background: var(--surface, #fff); color: var(--ink); font: inherit; font-size: 12px; }
.usage-detail-toggle { display: flex; align-items: center; gap: 6px; font-size: 12px; cursor: pointer; white-space: nowrap; }
.usage-count { margin-left: auto; font-size: 12px; color: var(--muted); }
.usage-note { margin: 14px 0 18px; color: var(--muted); font-size: 12px; line-height: 1.8; }
.usage-tabs { display: flex; flex-wrap: wrap; width: fit-content; max-width: 100%; padding: 3px; gap: 2px; border: 1px solid var(--line); border-radius: var(--radius-pill); background: var(--surface-muted); }
.usage-tabs button { flex: 0 0 auto; margin: 0; padding: 8px 12px; border: 1px solid transparent; border-radius: var(--radius-pill); background: transparent; color: var(--muted); font: inherit; font-size: 14px; cursor: pointer; }
.usage-tabs button[aria-selected="true"] { color: var(--ink); background: var(--accent-soft); font-weight: 550; }
.usage-tabs button:hover { color: var(--ink); }
.usage-table-wrap { overflow-x: auto; border: 1px solid var(--line); border-radius: 12px; }
.usage-table { width: 100%; min-width: 600px; table-layout: fixed; border-collapse: collapse; font-size: 12px; }
.usage-table th, .usage-table td { padding: 13px 10px; text-align: left; border-bottom: 1px solid var(--line); vertical-align: middle; }
.usage-table th { font-weight: 500; color: var(--muted); background: var(--bg); }
.usage-table:not(.usage-requests) th:first-child { width: 24%; }
.usage-requests { min-width: 680px; }
.usage-col-time { width: 15%; }
.usage-col-provider { width: 13%; }
.usage-col-model { width: 24%; }
.usage-col-token { width: 13%; }
.usage-col-cost { width: 13%; }
.usage-col-latency { width: 10%; }
.usage-col-status { width: 12%; }
.usage-requests th, .usage-requests td { padding: 12px; }
.usage-requests td:first-child { white-space: nowrap; font-variant-numeric: tabular-nums; }
.usage-requests .numeric { white-space: nowrap; overflow-wrap: normal; }
.usage-table tr:last-child td { border-bottom: 0; }
.usage-table .numeric { text-align: right; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.usage-truncate { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.usage-status { color: var(--muted); font-size: 11px; }.usage-status.succeeded { color: var(--green, #167b55); }.usage-status.error { color: var(--red, #b83d3d); }
.usage-table .usage-empty { padding: 42px 16px; text-align: center; color: var(--muted); line-height: 1.8; }
.usage-pagination { justify-content: flex-end; padding-top: 12px; font-size: 12px; color: var(--muted); }
.usage-detail-row td { background: var(--accent-soft, #edf3f2); }.usage-detail-row summary { cursor: pointer; overflow-wrap: anywhere; }.usage-detail-row dl { display: grid; grid-template-columns: 130px minmax(0, 1fr); gap: 8px; margin: 14px 0; }.usage-detail-row dd { margin: 0; overflow-wrap: anywhere; }.usage-detail-row dt { color: var(--muted); }
.usage-wrap { overflow-wrap: anywhere; }
.usage-pricing-form { display: grid; grid-template-columns: repeat(3,minmax(0,1fr)); gap: 16px; }
.usage-pricing-form label { display: flex; flex-direction: column; gap: 8px; font-size: 12px; min-width: 0; }.usage-pricing-form input, .usage-pricing-form select { width: 100%; min-width: 0; box-sizing: border-box; }
.usage-pricing-actions { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; grid-column: 1 / -1; }.usage-pricing-actions span { font-size: 12px; color: var(--muted); }.usage-save-error { color: var(--red, #b83d3d); }
@media (max-width: 1100px) { .usage-metrics { grid-template-columns: repeat(2,minmax(0,1fr)); }.usage-pricing-form { grid-template-columns: repeat(2,minmax(0,1fr)); } }
@media (prefers-reduced-motion: reduce) { .spinning { animation: none; } }
</style>
