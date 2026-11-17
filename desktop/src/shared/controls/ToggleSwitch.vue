<script setup>
import { computed } from "vue";
defineOptions({ inheritAttrs: false });
const props = defineProps({ modelValue: Boolean, checked: { type: Boolean, default: undefined } });
const emit = defineEmits(["update:modelValue", "change"]);
const enabled = computed(() => props.checked ?? props.modelValue);
function change(event) {
  emit("update:modelValue", event.target.checked);
  emit("change", event);
}
</script>

<template>
  <span class="switch-control">
    <input v-bind="$attrs" class="switch-native" type="checkbox" role="switch" :checked="enabled" @change="change">
    <span class="switch-track" aria-hidden="true"></span>
    <span class="switch-thumb" aria-hidden="true">
      <span class="switch-face switch-off"><span></span></span>
      <span class="switch-face switch-on"><svg viewBox="0 0 16 16"><path d="m4 8 3 3 5-6" /></svg></span>
    </span>
  </span>
</template>
