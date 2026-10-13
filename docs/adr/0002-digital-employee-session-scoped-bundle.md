# ADR 0002: Digital employees carry a session-scoped bundle

## Status

Accepted. Ratified design baseline (2026-10-09, owner-approved in the design
session). Recorded before implementation; the implementation reference is
[docs/digital-employee.md](../digital-employee.md).

## Context

MOVO compiles every turn from the speaking user's own account: their visible
model, their visible skills, their visible tools. Session sharing is built on
this rule — "sharing never transfers permissions" (docs/session-sharing.md,
"Everyone keeps their own permissions") — and ADR 0001 relies on it for
history fidelity. The web plane has no AGENTS.md-style bootstrap: the
agent-instructions plane is deliberately disabled for web presets, and the
only persona is the static MOVO identity, which forbids claiming any other
public name.

The product needed a named, governed agent persona — a digital employee such
as the gift collaborator 小礼, which carries a manifesto and a fixed set of
MCP tools. Under the per-speaker compilation rule such an employee cannot
exist: every speaker re-derives their own skill and tool sets, so two members
of one shared session would talk to two different "employees", and a persona
channel would still be missing.

Resource-level assignment in the community edition is the second gap: skills
and MCP tools are tenant-visible to everyone, and per-user resource narrowing
exists only as a commercial extension point. There is no community mechanism
that says "these skills and tools belong together, and only these people may
use them".

## Decision

A digital employee is a session bootstrap bundle and a unit in the permission
architecture. Five interlocking decisions define it:

1. **The bundle is session-scoped.** A session is bound to at most one digital
   employee at creation (first message). From then on the compiled skills and
   external tools of that session are exactly the employee's bound set for
   every speaker, and the manifesto is injected as its own system-prompt
   section. The binding is immutable; talking to another employee means
   starting a new conversation.
2. **Assignment grants the bundle.** Availability of an employee is `active`
   AND (`is_public` OR an assignment matches one of the user's position roles
   OR the user directly). Inside an employee session the bound set is not
   re-narrowed against the speaker's per-user resource visibility. This makes
   the employee the community-edition unit for "this bundle of resources is
   usable by these people".
3. **The capability floor stays per speaker.** The five platform capability
   gates (content, image, code, browser, internal knowledge) remain attached
   to the speaking user's position roles. An employee cannot grant, narrow or
   replace account powers; it only carries resources.
4. **Shared sessions inherit the employee.** Membership is the gate: any
   participant of a shared employee session talks to the same employee with
   the same bundle, without per-member assignment checks. This is the one
   deliberate exception to the per-speaker compilation rule; the share link
   is already a bearer credential within the tenant.
5. **Edits are live; the profile system carries the versioning.** The
   employee segment participates in the immutable runtime profile content
   hash, so manifesto or bundle edits rotate the session's runtime profile on
   the next turn, exactly like skill edits. No per-session employee snapshot
   is pinned in v1.

The static MOVO persona keeps all behavioral guardrails and gains one
exception clause: in a session configured with a digital employee the model
identifies itself exclusively as that employee. Token usage rows for employee
sessions carry the employee id next to the speaking user id, so cost can be
attributed per employee without touching per-user quotas.

## Alternatives considered

- **Narrow-only semantics** (bound set ∩ the speaker's own visibility): safer
  in isolation, but an assigned user missing one resource would silently run
  a reduced employee, and operators would maintain both the employee and
  per-user grants. Rejected: silent degradation is worse than an explicit
  grant unit.
- **Per-speaker availability checks in shared sessions**: rejected; it breaks
  "everyone talks to the same employee", and share links are already
  credential-equivalent within the tenant.
- **Pinned per-session employee snapshots** (bootstrap frozen at creation):
  closer to the AGENTS.md metaphor, but it requires employee versioning and
  diverges from how skill edits already behave (live on the next turn).
  Deferred; live semantics is v1.
- **Full persona replacement** by the manifesto: rejected; it would discard
  the static persona's tool-discipline guardrails and force every manifesto
  author to re-derive them.
- **Employee-bound model** ("a fixed brain"): deferred with a reserved field;
  model selection stays with the speaker in v1.

## Consequences

One governance point now defines a complete working identity: admins create
an employee once, and every authorized user — in private or shared sessions —
gets the same persona and the same bundle. The community edition gains a
resource-assignment unit it previously lacked. Shared sessions stay
consistent: the employee segment is stable across speaker rotations, while
capability gates, token attribution, approvals and cancel rights remain
per-speaker.

The accepted costs: the employee editor becomes a permission-granting surface
and stays behind the admin guard; personal skills and writing styles are
unavailable inside employee sessions; disabling an employee hard-blocks its
existing sessions (explicit error, readable history); and the prompt-level
identity exception must be QA'd so the model never falls back to calling
itself MOVO inside employee sessions. The blast radius is the profile
compiler and synchronizer, the runtime-host profile whitelist and prompt
sections, session serialization, both consoles, and token usage logs.
