# rekuest docs

Focused guides for rekuest patterns that aren't obvious from the API surface
alone.

- [Workflows](./workflows.md) — only a workflow calls other actions; it is resumed when
  its agent dies, a plain task ends LOST, and `effects=` informs whoever decides.
- [Agent dependencies](./agent-dependencies.md) — declare the actions and states
  an app depends on with `@app.declare` (methods are action demands, annotated
  attributes are state demands), and redirect individual demands to another
  app + key with `@demand` / `demand_state` from `rekuest.declare`.
- [Disconnect policy](./disconnect-policy.md) — declare what happens to an action's
  in-flight work when the agent loses its control channel (`CancelOnDisconnect`),
  and how hard the agent fights to keep it (`ConnectionPolicy`).
- [The agent journal](./journal.md) — one ordered, persisted record of everything an
  agent reports (task events, locks, state patches), resumable over the served
  websocket (`"journal": true`) and replayable at any position (`/journal…` routes).
  The contract shared with the Rust agent and the rekuest server.
