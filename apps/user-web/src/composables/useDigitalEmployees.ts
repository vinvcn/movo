import { ref } from 'vue'
import { fetchAvailableDigitalEmployees, type AvailableDigitalEmployee } from '../api/digitalEmployees'

/**
 * Ticket #42: the composer's digital-employee picker list. Tenant-scoped like
 * the chat-model list (useChatModels) and loaded only for a pane that has no
 * session yet — session panes take their identity from the session payload.
 */
export function useDigitalEmployees(options: { getMainId: () => string }) {
  const availableDigitalEmployees = ref<AvailableDigitalEmployee[]>([])
  const digitalEmployeesLoading = ref(false)
  const digitalEmployeeLoadError = ref('')

  async function loadDigitalEmployees() {
    if (digitalEmployeesLoading.value) return
    try {
      digitalEmployeesLoading.value = true
      digitalEmployeeLoadError.value = ''
      availableDigitalEmployees.value = await fetchAvailableDigitalEmployees(options.getMainId() || 'default')
    } catch (error: any) {
      availableDigitalEmployees.value = []
      digitalEmployeeLoadError.value = String(error?.message || error || '')
    } finally {
      digitalEmployeesLoading.value = false
    }
  }

  return {
    availableDigitalEmployees,
    digitalEmployeesLoading,
    digitalEmployeeLoadError,
    loadDigitalEmployees,
  }
}
