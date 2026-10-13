<script setup lang="ts">
import DigitalEmployeeShelf from './DigitalEmployeeShelf.vue'
import { t } from '../../composables/i18n'

defineProps<{
  mainId: string
}>()

const emit = defineEmits<{
  (event: 'summon', employeeId: string): void
}>()
</script>

<template>
  <div class="flex h-full w-full flex-col bg-[#f8fafc]">
    <header class="shrink-0 border-b border-gray-200 bg-white/80 backdrop-blur-sm">
      <div class="flex items-center gap-1 px-6 pt-4 lg:px-8">
        <h1 class="rounded-lg px-3 py-2 text-sm font-semibold text-blue-700">{{ t('app.sidebar.digital_employees') }}</h1>
      </div>
    </header>

    <div class="min-h-0 flex-1 overflow-y-auto pt-5 custom-scrollbar">
      <DigitalEmployeeShelf
        :main-id="mainId"
        @summon="(employeeId) => emit('summon', employeeId)"
      />
    </div>
  </div>
</template>

<style scoped>
.custom-scrollbar::-webkit-scrollbar {
  width: 6px;
}
.custom-scrollbar::-webkit-scrollbar-track {
  background: transparent;
}
.custom-scrollbar::-webkit-scrollbar-thumb {
  background: #e2e8f0;
  border-radius: 10px;
}
.custom-scrollbar::-webkit-scrollbar-thumb:hover {
  background: #cbd5e1;
}
</style>
