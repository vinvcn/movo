<script setup lang="ts">
import { computed } from 'vue'
import MessageAvatar from './MessageAvatar.vue'

// T9 shared-session author strip: avatar (image or deterministic initials) plus
// display name above a user-authored row. Presentation logic lives in the pure
// `resolveAuthorPresentation` helper via MessageAvatar, so the headless suite
// can assert it without a DOM.
const props = withDefaults(defineProps<{
  displayName: string | null
  avatarUrl: string | null
  showAvatar?: boolean
}>(), { showAvatar: true })

const label = computed(() => (props.displayName ?? '').trim())
</script>

<template>
  <div class="flex min-w-0 items-center gap-2" data-user-message-author>
    <MessageAvatar
      :display-name="displayName"
      :avatar-url="avatarUrl"
      :show-avatar="props.showAvatar"
      size="sm"
    />
    <span v-if="label" class="max-w-[12rem] truncate text-xs font-medium text-slate-500">{{ label }}</span>
  </div>
</template>
