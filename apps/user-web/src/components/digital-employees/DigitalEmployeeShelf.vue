<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  fetchAvailableDigitalEmployees,
  type AvailableDigitalEmployee,
} from '../../api/digitalEmployees'
import { digitalEmployeeInitial } from '../../composables/digitalEmployeeSession'
import { t } from '../../composables/i18n'

const props = defineProps<{
  mainId: string
}>()

const emit = defineEmits<{
  (event: 'summon', employeeId: string): void
}>()

const employees = ref<AvailableDigitalEmployee[]>([])
const loading = ref(false)
const loadError = ref('')

/**
 * 货架的数据源就是会话准入列表：服务端的 list_available 谓词
 * （active AND (public OR 已指派)）已经决定了谁能被召唤，前端不再二次筛选，
 * 否则会出现「货架上有、点进去建不了」的越权幻觉。
 */
async function loadEmployees() {
  if (loading.value) return
  loading.value = true
  loadError.value = ''
  try {
    employees.value = await fetchAvailableDigitalEmployees(props.mainId || 'default')
  } catch (error: any) {
    employees.value = []
    loadError.value = String(error?.message || error || '')
  } finally {
    loading.value = false
  }
}

const emptyText = computed(() => (
  loadError.value ? t('digital_employees.shelf.load_failed') : t('digital_employees.shelf.empty')
))

onMounted(loadEmployees)
</script>

<template>
  <div class="px-6 pb-10 lg:px-8">
    <p class="mb-5 max-w-3xl text-xs text-gray-500">{{ t('digital_employees.shelf.subtitle') }}</p>

    <div v-if="loading" class="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
      <div v-for="n in 8" :key="n" class="h-[132px] animate-pulse rounded-2xl border border-gray-100 bg-gray-50"></div>
    </div>

    <div
      v-else-if="employees.length === 0"
      class="rounded-3xl border border-dashed border-gray-200 bg-white py-16 text-center"
    >
      <p class="text-sm text-gray-500">{{ emptyText }}</p>
    </div>

    <div v-else class="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
      <article
        v-for="employee in employees"
        :key="employee.id"
        class="group relative flex flex-col rounded-2xl border border-gray-200 bg-white p-4 shadow-sm transition-all hover:border-blue-200 hover:shadow-lg hover:shadow-gray-200/40"
      >
        <div class="flex items-start gap-3">
          <img
            v-if="employee.avatar_url"
            :src="employee.avatar_url"
            :alt="employee.name"
            class="h-10 w-10 shrink-0 rounded-xl object-cover"
          />
          <span
            v-else
            class="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-blue-50 text-sm font-bold text-blue-600"
          >{{ digitalEmployeeInitial(employee.name) }}</span>
          <div class="min-w-0 flex-1">
            <h3 class="truncate text-sm font-bold text-gray-900 group-hover:text-blue-700">{{ employee.name }}</h3>
            <p class="mt-0.5 line-clamp-2 min-h-[32px] text-[11px] leading-relaxed text-gray-500">
              {{ employee.description || t('digital_employees.shelf.no_description') }}
            </p>
          </div>
        </div>

        <div class="mt-3 flex items-center justify-end border-t border-gray-50 pt-3">
          <button
            type="button"
            class="rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-200"
            @click="emit('summon', employee.id)"
          >
            {{ t('digital_employees.shelf.summon') }}
          </button>
        </div>
      </article>
    </div>
  </div>
</template>
