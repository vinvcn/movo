# Sharing sessions

Session sharing lets a session owner invite other users in the same
organization (tenant) to read and continue a conversation. Every member reads
the same full, author-labelled history and keeps talking in one thread, while
each member runs under their own model and their own tools and skills -
never the owner's.

## Share a session (owner)

1. Open the conversation. The chat top bar shows a share icon before anyone
   joins, then a members icon with the participant count. Both open the same
   dialog. The count excludes the owner.
2. Click the share icon to open the share dialog.
3. Choose how long the link stays valid (7, 30 or 90 days; 30 by default) and
   create the link.
4. Copy the link from the readonly input. The link looks like
   `<origin>/?session-share=<token>`.
5. Send the link to a colleague. Treat it like a credential: anyone in your
   organization who holds it can join while the share is active.

While the share is active, the owner can reopen the dialog to copy the link,
create a new link (which deactivates the previous one) or revoke sharing.
After a page reload the dialog no longer displays the link; creating a new
link is how you retrieve sharing.

## Open a shared link (recipient)

1. Open the link. Joining requires a MOVO account in the same organization as
   the session owner; a link does not work across organizations. If you are
   not logged in, the login dialog appears first and the link is preserved
   across the login.
2. After login the application joins the session once, automatically.
3. The conversation opens directly and appears in the sidebar under
   **Shared with me**. You read the full history, labelled by author.

If the link has expired, was revoked, or belongs to another organization, the
application shows an error message and lands on the normal home page.

## The "Shared with me" section

Sessions you have joined appear under a dedicated **Shared with me** section
below Conversations, alongside your own sessions but visually distinct. Each
row shows the owner's title and a Shared badge, and polls independently with
its own pagination. Shared rows have no delete or rename controls (those
belong to the owner) and no row-level leave action - leaving lives in the
members control in the chat top bar.

## Who can do what

| Action | Owner | Participant |
| --- | --- | --- |
| Read the full thread and send messages | Yes | Yes |
| Open the members list (members control) | Yes | Yes |
| Create, copy or revoke the share link | Yes | No |
| Remove a participant | Yes | No |
| Leave the session | No | Yes |
| Rename or delete the session | Yes | No |
| Cancel a running turn | Own runs only | Own runs only |
| Approve a tool call | Own runs only | Own runs only |

The share icon is present only for the owner before anyone joins; the members
control is present for every member after joining. Only server-run conversations can be shared: a conversation
that currently runs on a desktop connection, or that has no conversation
binding at all (legacy sessions), is rejected with an error when the owner
tries to share it. The share icon's visibility is a best-effort client check;
the server has the final say and rejects with a clear error. Desktop
conversations do not show the share surface.

## Everyone keeps their own permissions

Sharing never transfers permissions. Each participant's model, tools and
skills are compiled from their own account on every turn: a participant never
runs under the owner's model and never gains a tool or skill granted only to
the owner. Conversely, the owner gains nothing from the participant. A
running turn belongs to the member who started it: only they can stop it, and
tool-call approvals raised during a turn can only be listed and decided by
that same member.

Digital-employee sessions are the one deliberate exception to the first
sentence: a session bound to a digital employee compiles its skills and tools
from the employee's bound bundle for every speaker, so all members talk to
the same employee with the same bundle. See
[Digital employees](digital-employee.md) and
[ADR 0002](adr/0002-digital-employee-session-scoped-bundle.md) for the exact
semantics and the per-speaker capability floor that still applies.

## What shared history exposes

A participant reads the conversation's full history as it was produced,
including execution events, evidence bundles, images and documents generated
under the owner's grants, and file links signed by the platform. This is a
deliberate decision with known and accepted limitations - see
[ADR 0001: Full-fidelity history for shared sessions](adr/0001-session-sharing-full-fidelity-history.md)
for the exact entitlements and their boundaries.

## Removing a member is not a ban

Removing a participant (or a participant leaving) takes their access away
immediately: they can no longer read the session, send messages or see the
members list. It is not a permanent ban. A removed participant can re-join
through a share link that is still live; re-joining restores their
membership. To exclude someone permanently, revoke the share link and, if
you still want to share, create a new link and give it only to the members
you want.

## Unread signal and running turns

- Rows in **Shared with me** show a dot when new messages arrive; the dot
  clears when you open the session. Your own rows keep their existing unread
  behavior.
- A conversation runs one turn at a time. If someone else's run is in
  progress, sending shows a neutral notice instead of a duplicate entry, the
  composer stays available for a later retry, and you can follow the run's
  progress while it is running.
- The stop control is available only to the member who started the current
  run; other members see that a run is in progress.

## Operator notes

This section is for deployments and administrators. It describes the
feature's storage surface and the one remediation procedure that may be
required on upgrade.

### Storage surface

**New collection `session_participants`** - one row per participant:

| Field | Meaning |
| --- | --- |
| `main_id` | Tenant id (same as the session's `main_id`) |
| `conversation_id` | The shared session's id |
| `user_id` | The participant's user id |
| `role` | Always `"participant"` (the session owner never gets a row) |
| `joined_at` | When the participant joined (refreshed on re-join) |
| `last_read_seq` | The participant's read cursor for the unread signal |
| `removed_at` | Set when the participant is removed; null means active |

**New fields on `chat_sessions`** - `share_token_hash`, `share_expires_at`
and `share_revoked_at`. Only the SHA-256 hash of a share token is ever
stored; the raw token exists only in the link the owner copied and never in
application or gateway logs. Absent fields mean the session was never
shared; no backfill is needed.

**Ownership and membership.** A session's `user_id` remains the owner.
Membership means the viewer is the owner or holds an active
`session_participants` row (`removed_at` null). A row with `removed_at` set
grants no read and no write access and is retained only for history and
re-join bookkeeping.

### New indexes

| Index | Collection | Definition |
| --- | --- | --- |
| `unique_conversation_participant` | `session_participants` | unique `(conversation_id, user_id)` |
| `participant_memberships_by_user` | `session_participants` | `(main_id, user_id, joined_at desc)` |
| `unique_main_share_token_hash` | `chat_sessions` | partial unique `(main_id, share_token_hash)`, only where the field is a string |
| `unique_main_session_seq` | `chat_messages` | **unique partial** `(main_id, session_id, seq)` for rows with a string `main_id`, ObjectId `session_id`, and integer `seq` |

The indexes are created automatically by `./movo up` or `./movo update`
before Compose replaces a running chat-api. The same index checks run during
chat-api startup for fresh installations and deployments that bypass the CLI.
The upgrade creates indexes only: it does not rewrite or delete existing
sessions, messages, or participant rows. Re-running it is safe.

`unique_main_session_seq` makes a valid message's sequence number unique within
its session across all writers. Legacy rows missing tenant or sequence fields
remain outside the index and are not rewritten at startup. When valid rows
duplicate `(main_id, session_id, seq)`, the upgrade **stops before
replacing the running services** with a conflict report. It never silently
de-duplicates or re-sequences customer messages. The old application remains
available, and the data remains intact. Run `./movo fix` during a maintenance
window, followed by `./movo up`. A direct startup also aborts.
Deployments that already have the older full unique index keep using it;
startup does not replace that stronger index under the same name.

### Repairing genuinely duplicated message sequences

Normal customer upgrades require no manual database steps. When the automatic
check reports valid messages with the same sequence number, run `./movo fix`
and confirm the maintenance window. `fix` stops chat-api but leaves MongoDB
running, saves a compressed database archive under `backups/`, re-sequences
only affected conversations, updates summary ranges, read cursors and session
counters, then validates and creates the indexes. It does not delete messages
or change message content. Chat is unavailable from the stop until the
subsequent `./movo up` completes.

On repair or validation failure, `fix` restores the three chat collections
from the archive and restarts the old chat-api container. If archive restore
itself fails, chat-api stays stopped and the archive path is printed for
recovery. Do not run `./movo down` between `fix` and `up`: MongoDB must remain
available for the repair and verification steps.

### Serialized runs and initiator governance

- A conversation runs one turn at a time. A turn sent while another run is
  in progress is rejected with `409 session_already_running`.
- An open shared conversation shows which other member is running a turn and
  disables sending until it finishes. The member can then refresh the
  conversation in place to see the latest messages. The server-side `409`
  remains the guard for requests that race with the status update; the client
  restores the unsent draft when that happens.
- When the speaker changes, the platform re-profiles the conversation to the
  new speaker and seeds the successor runtime session from the predecessor.
  This rotation (create, seed, dispose) happens once per speaker change and
  is expected behavior, not a fault.
- A run's `active_run` records its `initiator_user_id`. Cancel is allowed
  only for that initiator; every other member - the owner included -
  receives `403 session_cancel_initiator_required`. Runs with no recorded
  initiator fail closed. Tool-call approvals are stamped with the same
  initiator id, and only that initiator can list or decide them.

### Token usage attribution for existing DSH logs

New DSH model-gateway credentials carry the profile's speaker identity, so
Token usage, department reports, and per-user quotas receive the correct
`user_id`. Earlier `dsh_agent_turn` records may have an empty `user_id`.
After a database backup, review and then repair one tenant's historical rows
using its kernel binding and same-tenant user record:

```bash
cd services/chat-api
venv/bin/python scripts/backfill_dsh_token_usage_users.py --main-id YOUR_MAIN_ID
venv/bin/python scripts/backfill_dsh_token_usage_users.py --main-id YOUR_MAIN_ID --apply
```

The script skips rows without a matching binding and same-tenant user. It
does not change model-connectivity-test rows.

### Scoping boundaries

Search, compaction, evidence, history RAG, pending-approval badges and quota
remain scoped to the calling user; a participant does not gain owner-row
results through them. See the ADR linked above for the full list of known
limitations and their accepted boundaries.

### Owner delete cascade

Deleting a shared session (owner only) removes its `chat_messages` and
`execution_logs` rows by session id regardless of author, disposes every
kernel binding for the conversation and deletes all `session_participants`
rows. Nothing is orphaned.

### MongoDB version

The partial unique index (`unique_main_share_token_hash`) requires MongoDB
3.2 or later. The shipped Compose image is `mongo:6.0.20`.
