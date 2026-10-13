# Digital employees

A digital employee is a session bootstrap bundle and a unit in the permission
architecture: what `AGENTS.md` is to a coding agent, made tenant-governed for
the web plane. Each digital employee carries a manifesto that defines a
session's identity, a curated set of organization skills and MCP tools, and an
availability rule that decides which users may start a session with it. A
session bound to a digital employee runs as that employee, and everyone in a
shared session talks to the same one.

Ratified design baseline (2026-10-09). This document is the implementation
reference for the digital-employee change;
[ADR 0002](adr/0002-digital-employee-session-scoped-bundle.md) records the
semantic decision behind the bundle model.

## What an employee is made of

| Field | Meaning |
| --- | --- |
| `name` | Display name, unique within the tenant. In employee sessions the model identifies itself with this name. |
| `description` | One-line subtitle in the user-side picker. |
| `avatar_url` | Optional. Empty falls back to the name's first letter. |
| `manifesto` | Markdown, at most 8 KB, injected into the system prompt of the employee's sessions. Trusted admin-authored content: never paste credentials into it. |
| `skill_ids` | Bound organization skills: the exact skill set of employee sessions. |
| `tool_bindings` | Bound external tools (HTTP/MCP), organization scope only, each with an optional subset of the MCP server's discovered tool names. |
| `is_public` | When true, every user of the tenant may use the employee; otherwise assignment gates availability. |
| `status` | `active` or `disabled`. There is no draft state: `disabled` doubles as the editing state. |

An empty bundle (no skills, no tools) is valid: the employee is then a pure
persona defined by its manifesto.

## Use a digital employee (end user)

1. A new conversation pane offers a digital-employee picker. It lists the
   employees available to you: active, and either public or assigned to one
   of your position roles or to you directly.
2. Pick one and send the first message. The session is created bound to that
   employee; the session header and the conversation list show its name and
   avatar.
3. The binding is immutable from the first message on. To talk to a different
   employee, start a new conversation.
4. Inside the session the model answers as the employee: it introduces itself
   by the employee's name and follows the manifesto. Retrieval discipline,
   tool-usage rules and the interaction-language policy are unchanged.
5. Personal skills and personal writing styles are not available inside
   employee sessions; the in-session skill picker lists only the employee's
   bound skills.

### Shared employee sessions

Sharing an employee session works like sharing any other session, with one
difference: the employee belongs to the session, not to the speaker. A
participant who was never assigned the employee can join and talk, and every
member talks to the same employee with the same bound skills and tools. Each
member's capability gates still apply to that member's own turns (see
[The capability floor](#the-capability-floor)). Token usage is attributed to
the member who sent the message and to the employee.

## Manage digital employees (admin)

The admin console gains a **Digital employees** page.

- **Create and edit** name, description, avatar, manifesto (with a size
  counter and the 8 KB limit), the public switch, and the status. The name
  must be unique within the tenant.
- **Bind skills and tools.** Skills come from the organization skills. Tools
  come from registered external tools and must be organization-scoped;
  personal tools cannot be bound, because a bound tool's credentials would
  travel to every speaker. For MCP tools, select a subset of the server's
  discovered tool names.
- **Assign availability.** Assign the employee to position roles or to
  individual users, or mark it public. Availability is the OR of both: a user
  can use an employee when it is public or when any assignment matches.
- **Disable or re-enable.** Disabling takes effect immediately (see
  [Errors](#errors)).
- **Delete.** Physical deletion is offered only for employees with no session
  references.
- **Edits are live.** Manifesto and bundle edits take effect on the next turn
  of every bound session, the same way skill edits behave today. There is no
  per-session pinning in v1.

The editor runs a reference health check: skills or tool names that were
deleted, disabled or lost in an MCP re-discovery are flagged, because the
runtime skips them.

## The bundle at runtime

### Exact set, granted by assignment

In an employee session the compiled skills and external tools are exactly the
employee's bound set. The set is not intersected with the speaker's own
resource visibility, and it is not unioned with the speaker's personal
resources. Assignment to a role or user is therefore a grant: being allowed
to use an employee means being allowed to use what it carries.

### The capability floor

The five platform capability gates - content generation, image generation,
code generation, browser automation, internal knowledge - stay attached to
the speaking user's position roles, exactly as today. An employee cannot
grant, narrow or replace them. A speaker without browser automation never
receives the browser tool in any session, employee or not; a speaker without
internal knowledge never receives knowledge search. The employee is a bundle
of resources, not a bundle of account powers.

### Reference hygiene

When a bound skill or MCP tool name disappears (disabled, deleted, or lost in
a re-discovery), the runtime profile compiles without it and logs a warning;
the session continues with the reduced bundle. The admin editor flags the
dangling reference so it can be fixed or removed.

### Prompt injection

The manifesto is injected as its own system-prompt section between the static
MOVO persona and the per-turn context. The static persona keeps its
behavioral guardrails and gains one exception clause: in a session configured
with a digital employee, the model identifies itself exclusively as that
employee. Everything else in the persona - retrieval choices, tool
discipline, progress notes - is retained.

### Versioning

The employee segment (id, name, manifesto) is part of the immutable runtime
profile snapshot and its content hash. Editing an employee therefore rotates
the session's runtime profile on the next turn, exactly like a skill edit
does. Bound sessions always run a profile whose employee segment matches the
employee at compile time; there is no pinned historical snapshot in v1.

## Availability rules

A user may start an employee session when the employee is `active` AND
(`is_public` OR an assignment matches one of the user's position roles OR an
assignment matches the user directly). Dangling assignments (a role that was
deleted) never match. An employee id that is not available to the caller is
treated as nonexistent - the same existence-hiding semantics as shared
sessions.

## Errors

| Situation | Behavior |
| --- | --- |
| Employee id unavailable, unknown, or disabled at session creation | Treated as nonexistent; the request is rejected. |
| Request carries an employee id that differs from the session's binding | Rejected with an explicit conflict, so a UI bug surfaces instead of silently switching identity. |
| Employee disabled while sessions are bound | New sessions cannot select it. Existing sessions keep readable history; the next send fails with an explicit "digital employee disabled" error. Re-enabling restores. |
| Deleting an employee with session references | Rejected with a conflict; disable instead. Zero-reference employees can be deleted. |
| Bound reference missing at compile time | Skipped with a warning; the session continues with the reduced bundle. |

Employee sessions run on server execution only. Desktop `code` tasks do not
inherit the employee.

## Cost attribution

Token usage rows for employee sessions carry the employee's id next to the
speaking user's id, so admin statistics can break cost down per digital
employee while per-user attribution and quotas stay unchanged.

## Operator notes

This section is for deployments and administrators. It describes the feature's
storage surface and operational behavior.

### Storage surface

**New collection `digital_employees`** - one row per employee:

| Field | Meaning |
| --- | --- |
| `main_id` | Tenant id |
| `name`, `description`, `avatar_url` | Presentation fields; `name` unique per tenant |
| `manifesto` | The system-prompt text, at most 8 KB |
| `skill_ids` | Bound organization skill ids |
| `tool_bindings` | `[{tool_id, enabled_tool_names}]`; organization-scoped tools only |
| `status` | `active` or `disabled` |
| `is_public` | Public availability switch |
| `created_by`, `created_at`, `updated_by`, `updated_at` | Audit fields |

**New collection `digital_employee_assignments`** - one row per assignment:

| Field | Meaning |
| --- | --- |
| `main_id` | Tenant id |
| `digital_employee_id` | The assigned employee |
| `subject_type` | `role` (a position role) or `user` |
| `subject_id` | The role or user id |
| `created_by`, `created_at` | Audit fields |

**New fields** - `chat_sessions.digital_employee_id` (set at creation, never
changed) and `token_usage_logs.digital_employee_id`.

**New segment in runtime profiles** - the immutable snapshot gains a
`digital_employee` segment (employee id, name, manifesto), which participates
in the profile content hash and travels to the runtime host as
`employeeProfile`.

### Indexes

| Index | Collection | Definition |
| --- | --- | --- |
| `unique_main_employee_name` | `digital_employees` | unique `(main_id, name)` |
| `employees_by_main_status` | `digital_employees` | `(main_id, status)` |
| `unique_employee_assignment` | `digital_employee_assignments` | unique `(main_id, digital_employee_id, subject_type, subject_id)` |
| `assignments_by_subject` | `digital_employee_assignments` | `(main_id, subject_type, subject_id)` |
| `sessions_by_employee` | `chat_sessions` | partial `(main_id, digital_employee_id)` where the field is a string |
| `token_usage_by_employee` | `token_usage_logs` | partial `(main_id, digital_employee_id)` where the field is a string |

Indexes are created idempotently at startup and by the CLI update path, like
the session-sharing indexes. No backfill is needed: absent fields mean "never
an employee session".

### Rollback

Reverting the deployment is safe. Old code ignores the new fields; at its next
turn each employee session recompiles without the employee segment and
continues as a normal session (an automatic profile rotation, no data loss).
Employee rows remain in place and can be re-used after rolling forward.

### Acceptance walkthrough

The end-to-end walkthrough for the feature, in order:

1. Create an employee "小礼": manifesto, five gift MCP tools, a bound skill,
   assigned to the internal gift role, not public.
2. A role member sees it in the picker; an unassigned user does not.
3. In the session the model calls itself 小礼; the tool set equals the bound
   bundle; the runtime profile snapshot contains the employee segment.
4. Share with an unassigned user: they can read and talk; both speakers run
   the same bundle.
5. Capability floor: in the same shared session, a speaker without browser
   automation never receives the browser tool; a speaker with the capability
   does.
6. Edit the manifesto: the next turn reflects it, observable as a profile
   version rotation.
7. Disable: new sessions cannot select it; existing sessions reject sends
   with a clear error while history stays readable; re-enable restores.
8. Deleting the referenced employee is rejected with a conflict; a
   zero-reference employee deletes.
9. A public employee is visible to users with no assignment.
10. Admin cost statistics show usage per employee; per-user attribution is
    unchanged.

The archived walkthrough report — per-step expected/actual/evidence, the
stack bring-up recipe, and the 2026-10-09 Playwright run result — is
[qa/digital-employee-walkthrough.md](../qa/digital-employee-walkthrough.md);
the UI walkthrough itself is [qa/digital-employee.spec.ts](../qa/digital-employee.spec.ts).

### Example bundle

The real-world example that drove this design — 小礼, the gift collaborator —
lives in the demo seed package maintained alongside this work (a manifesto,
one installed skill package, the gift MCP registration, an avatar and a
position role, applied to a running stack through the real admin API by an
idempotent apply script); it is not part of this change. Nothing here depends
on it: the walkthrough above creates its employee through the real admin
console.
