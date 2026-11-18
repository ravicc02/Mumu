<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";

defineOptions({ inheritAttrs: false });
const props = defineProps({
  modelValue: { type: String, default: "all" },
  items: { type: Array, required: true },
  label: { type: String, default: "视图" },
});
const emit = defineEmits(["update:modelValue"]);
const root = ref(null);
const indicator = ref(null);
let observer;
let mounted = false;

const enabledIndexes = computed(() => props.items.map((item, index) => item.disabled ? -1 : index).filter(index => index >= 0));
function select(item) {
  if (item.disabled) return;
  emit("update:modelValue", item.value);
}
async function measure() {
  await nextTick();
  const selected = root.value?.querySelector('[aria-selected="true"]');
  if (!selected || !indicator.value) return;
  indicator.value.style.width = selected.offsetWidth + "px";
  indicator.value.style.transform = "translateX(" + selected.offsetLeft + "px)";
  if (root.value) root.value.dataset.ready = String(mounted);
  mounted = true;
}
function nextEnabled(index, direction) {
  if (!enabledIndexes.value.length) return index;
  const current = enabledIndexes.value.indexOf(index);
  const start = current >= 0 ? current : direction > 0 ? -1 : enabledIndexes.value.length;
  return enabledIndexes.value[(start + direction + enabledIndexes.value.length) % enabledIndexes.value.length];
}
function navigate(event, index) {
  let next;
  if (event.key === "ArrowRight") next = nextEnabled(index, 1);
  else if (event.key === "ArrowLeft") next = nextEnabled(index, -1);
  else if (event.key === "Home") next = enabledIndexes.value[0];
  else if (event.key === "End") next = enabledIndexes.value.at(-1);
  else return;
  event.preventDefault();
  const item = props.items[next];
  if (!item || item.disabled) return;
  select(item);
  root.value?.querySelectorAll('[role="tab"]')[next]?.focus();
}
watch(() => [props.modelValue, props.items], measure, { deep: true });
onMounted(() => {
  if (typeof ResizeObserver !== "undefined" && root.value) {
    observer = new ResizeObserver(measure);
    observer.observe(root.value);
  }
  measure();
});
onBeforeUnmount(() => observer?.disconnect());
</script>

<template>
  <div ref="root" class="ui-tabs" role="tablist" :aria-label="label" v-bind="$attrs">
    <span ref="indicator" class="ui-tabs-indicator" aria-hidden="true"></span>
    <button v-for="(item, index) in items" :key="item.value" type="button" role="tab"
      :aria-selected="modelValue === item.value" :aria-disabled="item.disabled || undefined"
      :tabindex="modelValue === item.value && !item.disabled ? 0 : -1"
      :disabled="item.disabled" :class="{ selected: modelValue === item.value }"
      @click="select(item)" @keydown="navigate($event, index)">
      <component :is="item.icon" v-if="item.icon" :size="14" aria-hidden="true" />
      {{ item.label }}<span v-if="item.count !== undefined" class="ui-tab-count">{{ item.count }}</span>
    </button>
  </div>
</template>
