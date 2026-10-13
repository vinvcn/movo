const CONTEXT_NAME = 'askai:digital-employee'

export function renderEmployeeContext(employee) {
  if (employee === undefined) return ''
  const sections = [
    `MOVO session identity: this conversation is attended by the admin-configured digital employee ${employee.name}. Identify yourself to users exclusively as ${employee.name} - never as MOVO - and never expose internal project, service, protocol, or implementation codenames.`,
  ]
  if (employee.manifesto) {
    sections.push(`Digital employee ${employee.name} manifesto (trusted, admin-authored). It defines this employee's persona, responsibilities and boundaries for this session:\n\n${employee.manifesto}`)
    sections.push('The manifesto does not widen permissions: a tool that is not available in this session simply does not exist for you. The remaining MOVO operating rules still apply unless the manifesto narrows them.')
  }
  return `MOVO digital-employee scope:\n${sections.join('\n\n')}`
}

/**
 * Per-runtime system-prompt section for a session-scoped digital employee.
 *
 * Unlike RuntimeTurnContext the employee scope is bound to the immutable
 * Runtime Profile, not to per-turn data: every session living in this
 * runtime belongs to the same employee, so the rendered text is constant
 * for the runtime's lifetime. An absent employee renders an empty section
 * and contributes nothing to the prompt.
 */
export class RuntimeEmployeeContext {
  #employee
  #dispose

  constructor(employee) {
    this.#employee = employee
  }

  install(ctx) {
    if (this.#dispose !== undefined) throw new Error('runtime employee context is already installed')
    this.#dispose = ctx.systemPrompt.context({
      name: CONTEXT_NAME,
      // Order 10: after the static persona (0), before the temporal (20) and
      // per-turn (30) sections, so turn scope can still refine behavior.
      order: 10,
      text: () => renderEmployeeContext(this.#employee),
    })
  }

  dispose() {
    this.#dispose?.()
    this.#dispose = undefined
  }
}
