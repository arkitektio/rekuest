# The agent journal

One ordered record of everything an agent reports: task events, lock changes, state patches, snapshots, the session baseline, and (when served) the `ASSIGN` that started each task. The record is persisted, and any position in it can be replayed.

This document is the contract shared by the Rust agent (`rekuest` crate), the Python agents (the journal itself in `arkitekt-runtime`, the distributed agent in `rekuest`, the served agent and its sqlite store in `arkitekt-fastapi`) and the rekuest server.

## Why

Before the journal, there were two unrelated counters:

- **`global_rev`** numbered state patches and was persisted.
- **`seq`** numbered task events. It was per connection, restarted on reconnect, and was not persisted.

No key related a `YIELD` to the patches around it. On top of that, several races meant the wire order was not the causal order:

- `seq` was taken before the broadcast lock.
- Python sent task events directly but queued patches, so a `COMPLETED` could overtake its patches.
- A cancelled task could still publish a patch after its `CANCELLED`.

## Entries

| field | |
|---|---|
| `session_id` | the agent session (a new one per process start) |
| `pos` | 1, 2, 3, … per session, no gaps; `(session_id, pos)` is the durable key |
| `global_rev` | the state revision **after** this entry (patches bump it; everything else carries the current value) |
| `timepoint` / `event_time` | when the agent recorded it (ISO 8601 / epoch ms) |
| `kind` | the wire `type`: `ASSIGN`, `PROGRESS`, `LOG`, `YIELD`, `STARTED`, `PAUSED`, `RESUMED`, `COMPLETED`, `FAILED`, `CRITICAL`, `CANCELLED`, `INTERRUPTED`, `EFFECT`, `LOCK`, `UNLOCK`, `SHELVE`, `UNSHELVE`, `STATE_PATCH`, `STATE_SNAPSHOT`, `SESSION_INIT` |
| `step` | the entry's step in its task (`task_step` on the wire): 1, 2, 3, … per task this process runs, shared with the task's child calls; none for `ASSIGN`, for entries of no task, and for a task this process never ran |
| `task_id` | the task the entry belongs to (`STATE_PATCH`: the changing task; `UNLOCK`: the task that held the lock) |
| `action_key` | the task's action key (`interface`, else `action`, else `task`) |
| `subject` | the state (`STATE_PATCH`) or lock key (`LOCK`/`UNLOCK`) |
| `message_id` | the frame's `id`, the same one that went on the wire |
| `payload` | the frame, without the stream-level `seq` (and without `token` for `ASSIGN`) |

`REGISTER` and `HEARTBEAT_ANSWER` are not journaled. Messages before the first session are passed on unrecorded.

**The world at `pos` P:**

- **States:** the last `SESSION_INIT`/`STATE_SNAPSHOT` entry at or before P, plus the `STATE_PATCH` entries after it, up to P.
- **Tasks and locks:** every entry up to P, folded.

## Ordering rules

1. `pos` assignment, the persistence enqueue and the hand-off to the transport happen under **one lock**. Delivery order is therefore `pos` order.
2. Within a task, program order holds: `ASSIGN` < `PROGRESS`/`LOCK` < patches, `YIELD`s, … < the terminal event < `UNLOCK`.
3. **Nothing is recorded for a task after its terminal entry.** Each task has a *gate*:
   - Reports and state changes enter the gate for their synchronous duration.
   - The check happens before anything is changed, so a multi-op update is all or nothing.
   - The terminal report (at the end of a run, or on cancel/interrupt) closes the gate first. Closing refuses new entries and waits for those in flight.
   - Entering twice on one thread is allowed; for example, a log inside an update closure.
   - `LOCK`/`UNLOCK` are not gated: a lock is always released, after the end.
4. A `STATE_SNAPSHOT` at revision N comes before the patch that reaches N+1 and already contains it (as before).

## Storage (SQLite, same file as the state history)

```sql
CREATE TABLE IF NOT EXISTS journal (
    session_id TEXT NOT NULL,
    pos INTEGER NOT NULL,
    global_rev INTEGER NOT NULL,
    event_time INTEGER NOT NULL,          -- epoch milliseconds
    kind TEXT NOT NULL,
    task_id TEXT,
    action_key TEXT,
    subject TEXT,
    message_id TEXT NOT NULL,
    payload TEXT NOT NULL,                -- JSON
    PRIMARY KEY (session_id, pos),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);
CREATE INDEX IF NOT EXISTS idx_journal_task ON journal(task_id, session_id, pos);
CREATE INDEX IF NOT EXISTS idx_journal_time ON journal(session_id, event_time);
CREATE INDEX IF NOT EXISTS idx_journal_kind ON journal(session_id, kind, pos);
```

- Inserts are `INSERT OR IGNORE`, so a re-sent entry is a no-op.
- The `sessions`, `state_snapshots` and `state_patches` tables are still written as before, so the existing history routes are unchanged.

## HTTP routes (served agent)

`{session_id}` may be `current`.

| route | |
|---|---|
| `GET /journal` | the watermark `{session_id, pos, global_rev}` |
| `GET /journal/{session_id}?after=&until=&limit=&kinds=&task_id=&action_keys=&state_keys=&lock_keys=` | `{session_id, after, last_pos, entries}` in `pos` order. Key filters route like the websocket: patches by state, locks by key, session-wide entries always, the rest by action key. |
| `GET /journal/{session_id}/at/{pos}` | `{session_id, pos, global_rev, timepoint, entry, states, tasks, locks}` as of `pos` |
| `GET /journal/{session_id}/at?timestamp=` | the same at the last entry at or before a time (epoch ms or RFC 3339) |
| `GET /tasks/{task_id}/events` | `{task_id, task, entries}`. Works after the task has ended, unlike `/tasks/{task_id}`. |
| `GET /session_info` | also has `current_pos` |

**A task, folded:**
- Fields: `task`, `action_key`, `interface`, `reference`, `status`, `done`, `progress`, `message`, `error`, `yields`, `last_returns`, `first_pos`, `last_pos`.
- `status` is one of `ASSIGNED`, `RUNNING`, `PAUSED`, or the terminal kind.

**Locks** map key → holding task.

## Websocket opt-in (served agent)

A client that sends `"journal": true` in its first frame opts in:

```json
{"type": "INIT", "journal": true, "resume_after": 41, "session_id": "…", "action_keys": ["…"]}
```

- **The reply INIT** keeps Python's fields and adds a `journal` object. It is taken **under the journal lock**, so it is consistent with the watermark:
  ```json
  "journal": {"session_id": "…", "pos": 57, "global_rev": 12, "resync": false,
              "states": {…}, "tasks": {…}, "locks": {…}}
  ```
- **Every following frame** is the Python frame plus `pos` and `journal_session`.
  - The names are chosen not to clash: state frames already have `session_id`, and `STATE_PATCH` has `ts`.
  - Journal subscribers also get the session-wide frames (`SESSION_INIT`, `STATE_SNAPSHOT`) and `ASSIGN`.
- **With `resume_after: N`:** the server first sends the entries in `(N, pos]` that match the filters (from memory, else from storage). Then it sends live frames after `pos`. Nothing is missed or repeated.
  - Replayed frames have no `seq`, which is stream-level.
- **`resync: true`** is sent, with no backlog, when `N` is ahead of the watermark or `session_id` is not the current session. `session_id` is required with `resume_after` unless `N` is 0 (replay from the start): a position alone may be from before a restart. Drop local state and use the INIT.
- **A client that does not opt in** gets exactly Python's frames, as before.

## Agent ↔ server (remote agents)

The wire contract is the server's: rekuest's `docs/design/journal.md`, with every frame
in `tests/fixtures/agent_wire.json` (copied into this package's `tests/fixtures`). In short,
for the distributed agent (`rekuest.agents.agent.RekuestAgent`):

- Every numbered frame carries `pos`, `journal_session`, `agent_ts` and, for a task's frame,
  `task_step`. A child call takes its parent's next step and is sent as
  `ASSIGN_REQUEST {parent: task, parent_step: step}` (the `reference` stays the caller's own;
  the server stores the step as the child's `parent_step`, idempotent on `(parent, parent_step)`). A probe's (`p-…`) reports are never numbered.
- Every numbered frame is retained until a cumulative `JOURNAL_ACK` covers it (terminal
  reports too; `EVENT_ACK` is ignored), in a SQLite file (`$REKUEST_JOURNAL_PATH`, else
  `.arkitekt/rekuest_journal.db`) so it survives a restart. A new session never drops an
  earlier one's frames.
- Nothing is sent between `REGISTER` and `INIT`. After `INIT` the retained frames go out
  first, in `(session created, pos)` order, then everything new.
- `shelve` mints the drawer's `resource_id` itself and sends a numbered `SHELVE`; nothing
  is awaited. `COLLECT` names drawers by `resource_id`; dropping one sends a numbered
  `UNSHELVE`.
