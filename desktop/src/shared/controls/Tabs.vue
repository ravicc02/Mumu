<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";

const props = defineProps({
  modelValue: { type: String, default: "all" },
  items: { type: Array, required: true },
  label: { type: String, default: "视图" }
});
const emit = defineEmits(["update:modelValue"]);
const root = ref(null);
const indicator = ref(null);
let observer;
let mounted = false;
async function measure() {
  await nextTick();
  const selected = root.value?.querySelector('[aria-selected="true"]');
  if (!selected || !indicator.value) return;
  // Only transform animates; label or count changes resize the marker immediately.
  indicator.value.style.width = selected.offsetWidth + "px";
  indicator.value.style.transform = "translateX(" + selected.offsetLeft + "px)";
  root.value.dataset.ready = String(mounted);
  mounted = true;
}
function navigate(event, index) {
  let next = index;
  if (event.key === "ArrowRight") next = (index + 1) % props.items.length;
  else if (event.key === "ArrowLeft") next = (index + props.items.length - 1) % props.items.length;
  else if (event.key === "Home") next = 0;
  else if (event.key === "End") next = props.items.length - 1;
  else return;
  event.preventDefault();
  emit("update:modelValue", props.items[next].value);
  root.value.querySelectorAll('[role="tab"]')[next]?.focus();
}
watch(() => [props.modelValue, props.items], measure, { deep: true });
onMounted(() => {
  observer = new ResizeObserver(measure);
  observer.observe(root.value);
  measure();
});
onBeforeUnmount(() => observer?.disconnect());
</script>

<template>
  <div ref="root" class="ui-tabs" role="tablist" :aria-label="label">
    <span ref="indicator" class="ui-tabs-indicator" aria-hidden="true"></span>
    <button v-for="(item, index) in items" :key="item.value" type="button" role="tab"
      :aria-selected="modelValue === item.value" :tabindex="modelValue === item.value ? 0 : -1"
      :class="{ selected: modelValue === item.value }" @click="emit('update:modelValue', item.value)"
      @keydown="navigate($event, index)">
      <component :is="item.icon" v-if="item.icon" :size="14" aria-hidden="true" />
      {{ item.label }}<span v-if="item.count !== undefined" class="ui-tab-count">{{ item.count }}</span>
    </button>
  </div>
</template>
