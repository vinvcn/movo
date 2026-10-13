<script setup lang="ts">
import { computed } from 'vue'
import { resolveAuthorPresentation } from './userMessageAuthor'

/**
 * 消息行头像：助手行取本会话绑定的数字员工身份（无头像时退化为首字），
 * 未绑定时落到「普通员工」通用人像；用户消息优先头像图，缺失时退化为
 * 确定性首字（与 UserMessageAuthor 共享同一纯函数）。
 */
const props = withDefaults(defineProps<{
  displayName: string | null
  avatarUrl: string | null
  fallbackIcon?: string | null
  fallbackGlyph?: 'person' | null
  size?: 'sm' | 'md'
  showAvatar?: boolean
}>(), { fallbackIcon: null, fallbackGlyph: null, size: 'md', showAvatar: true })

const presentation = computed(() => {
  if (props.fallbackGlyph === 'person') return { kind: 'glyph' as const }
  if (props.fallbackIcon) {
    return { kind: 'image' as const, src: props.fallbackIcon, alt: props.displayName ?? '' }
  }
  return resolveAuthorPresentation({ display_name: props.displayName, avatar_url: props.avatarUrl })
})
const image = computed(() => (presentation.value.kind === 'image' ? presentation.value : null))
const initials = computed(() => (presentation.value.kind === 'initials' ? presentation.value : null))
const glyph = computed(() => (presentation.value.kind === 'glyph' ? 'person' : null))
const boxClass = computed(() => (props.size === 'sm' ? 'h-6 w-6' : 'h-7 w-7'))
</script>

<template>
  <template v-if="props.showAvatar">
    <img
      v-if="image"
      :src="image.src"
      :alt="image.alt"
      class="shrink-0 rounded-full border border-slate-200 bg-white object-cover"
      :class="boxClass"
      loading="lazy"
    />
    <span
      v-else-if="glyph === 'person'"
      class="flex shrink-0 items-center justify-center rounded-full border border-slate-200 bg-slate-100 text-slate-500"
      :class="boxClass"
      role="img"
      :aria-label="props.displayName || ''"
    >
      <svg class="h-1/2 w-1/2" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path>
        <circle cx="12" cy="7" r="4"></circle>
      </svg>
    </span>
    <span
      v-else-if="initials"
      class="flex shrink-0 items-center justify-center rounded-full bg-slate-200 text-[11px] font-semibold text-slate-600"
      :class="boxClass"
      role="img"
      :aria-label="initials.ariaLabel"
    >{{ initials.text }}</span>
  </template>
</template>
