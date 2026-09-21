# rekuest docs

Focused guides for rekuest patterns that aren't obvious from the API surface
alone.

- [Agent dependencies](./agent-dependencies.md) — declare the actions and states
  an app depends on with `@app.declare` (methods are action demands, annotated
  attributes are state demands), and redirect individual demands to another
  app + key with `@demand` / `demand_state` from `rekuest.declare`.
- [Disconnect policy](./disconnect-policy.md) — declare what happens to an action's
  in-flight work when the agent loses its control channel (`CancelOnDisconnect`),
  and how hard the agent fights to keep it (`ConnectionPolicy`).
