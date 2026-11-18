<script setup>
import { computed } from "vue";
defineOptions({ inheritAttrs: false });
const props = defineProps({
  modelValue: { type: [Boolean, Array], default: false },
  checked: { type: Boolean, default: undefined },
  value: { type: [String, Number], default: "on" },
  indeterminate: Boolean,
  pending: Boolean,
});
const emit = defineEmits(["update:modelValue", "change"]);
const selected = computed(() => props.checked ?? (Array.isArray(props.modelValue) ? props.modelValue.includes(props.value) : props.modelValue));
function change(event) {
  const checked = event.target.checked;
  const value = Array.isArray(props.modelValue)
    ? checked ? [...new Set([...props.modelValue, props.value])] : props.modelValue.filter(item => item !== props.value)
    : checked;
  emit("update:modelValue", value);
  emit("change", event);
}
</script>

<template>
  <span class="choice-control" :class="{ pending }">
    <input v-bind="$attrs" class="choice-native" type="checkbox" :value="value" :checked="selected" :indeterminate="indeterminate" :aria-checked="indeterminate ? 'mixed' : selected" :aria-busy="pending || undefined" @change="change">
    <span class="choice-box" aria-hidden="true">
      <svg viewBox="0 0 24 24"><path v-if="indeterminate" class="choice-mixed" d="M6 12h12" /><path v-else d="m5 12 4 4L19 6" /></svg>
    </span>
  </span>
</template>
