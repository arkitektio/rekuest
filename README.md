# rekuest

[![codecov](https://codecov.io/gh/arkitektio/rekuest/graph/badge.svg?token=xzxX2AQPmS)](https://codecov.io/gh/arkitektio/rekuest)
[![PyPI version](https://badge.fury.io/py/rekuest.svg)](https://pypi.org/project/rekuest/)
[![PyPI pyversions](https://img.shields.io/pypi/pyversions/rekuest.svg)](https://pypi.python.org/pypi/rekuest/)

**The distributed runtime for [Arkitekt](https://arkitekt.live) apps.**

An Arkitekt app is a declaration: the actions, states and hooks it offers. The
declaration itself is [arkitekt-spec](https://github.com/arkitektio/arkitekt-spec)'s,
and executing it locally is [arkitekt-runtime](https://github.com/arkitektio/arkitekt-runtime)'s.
rekuest adds what running it *distributed* takes:

- an **agent** that registers the declaration with a rekuest server over a websocket,
  is assigned tasks, and reports their progress, logs and results back;
- the **caller** through which one app's actions call other apps' actions via the server;
- **`Rekuest`**, the GraphQL client for the server: find actions and implementations,
  call them, and watch their tasks.

You rarely import rekuest yourself. `run(app)` in [arkitekt](https://github.com/arkitektio/arkitekt)
uses it, and the names an app is written with (`App`, `Task`, the `Annotated` markers,
the policies) come from `arkitekt`.

## Install

```bash
pip install "arkitekt[rekuest]"   # what an app needs to run(app)
pip install rekuest               # just the runtime and client
```

The optional extras are `units` (physical-quantity ports, via kanne) and `types`
(`annotated-types` constraints). rekuest requires Python 3.11+.

## Offering actions

Declare the app with arkitekt and run it. `run` connects it through rekuest's provider,
which builds the agent and keeps it offering the app's actions until you stop it:

```python
from arkitekt import App, Task, run

app = App("measure", "0.1.0")


@app.action
def double(x: int, task: Task) -> int:
    """Double

    Doubles a number.
    """
    task.log(f"doubling {x}")
    return x * 2


if __name__ == "__main__":
    run(app)
```

An app can also name the provider explicitly: `App(..., providers=[rekuest_provider])`
with `from rekuest.arkitekt import rekuest_provider`.

## Calling actions

From a script, name the rekuest service and call an action by its id (or pass an
`Action`/`Implementation` you looked up). The call is a *root* task:

```python
from arkitekt import easy
from rekuest.arkitekt import rekuest_service

with easy("my-script", rekuest_service) as rekuest:
    result = rekuest.call("<action-id>", x=21)
```

Only a **workflow** may call other actions. A call made inside one is a *child* of the
task it runs in, so it goes through the task: resolve the target with the client, then
call it on the task. A plain `@app.action` that calls raises `NotAWorkflowError`, and
`rekuest.acall` inside a task raises `RootOnlyCallError`:

```python
from arkitekt import App, Task
from rekuest.arkitekt import rekuest_service
from rekuest.client.client import Rekuest

app = App("orchestrate", "0.1.0", services=[rekuest_service])


@app.workflow
async def double_twice(x: int, action_id: str, task: Task, rekuest: Rekuest) -> int:
    """Double Twice"""
    action = await rekuest.aresolve(action_id)
    once = await task.acall(action, x=x)
    return await task.acall(action, x=once)
```

Usually a workflow names what it needs of another app as a protocol instead
(`@app.declare`, see [Agent dependencies](docs/agent-dependencies.md)) and calls its
methods; a method annotated as a generator streams the other action's yields.

Actions, implementations, shortcuts, test cases, test results and task events travel
between actions by id (`@rekuest/action`, `@rekuest/implementation`, …), so an action
can take and return them directly.

## When an agent dies

Nothing is re-run behind your back.

- **A plain task** whose agent dies mid-run ends **LOST**: not failed, its fate unknown.
  `rekuest.call` raises `AgentLost` with what is known (`started`, `last_progress`, and the
  action's `effects`), and whoever called decides. Work that was never picked up is simply
  delivered again.
- **A workflow** is **resumed**: it runs again from the top, but the calls it already made
  return their recorded results, and `task.now()`, `task.random()`, `task.sleep()` and
  `task.record(fn)` return their recorded values. Inside it, a lost step raises `AgentLost`,
  and `task.retry`, `task.hold` and `task.guard` cover the usual answers.

`effects=` on an action (`NONE`, `REPEATABLE`, `UNKNOWN`, `IRREVERSIBLE`) says what running it
again would do. It is information for whoever decides, never a rule the server acts on.
See [Workflows](docs/workflows.md).

## Package layout

| Module | What it is |
| --- | --- |
| `rekuest.arkitekt` | The service (`rekuest_service`), the agent provider (`rekuest_provider`) and the `@rekuest/*` structures |
| `rekuest.agents` | The agent, its websocket transport, control channel and caller |
| `rekuest.client` | `Rekuest`, the GraphQL client, and its postman |
| `rekuest.api.schema` | The generated GraphQL operations and types |
| `rekuest.protocol` | The wire protocol the agent speaks with the server |

## Guides

- [Workflows](docs/workflows.md) — calling other actions, and what happens when an agent dies
- [Agent dependencies](docs/agent-dependencies.md) — declare the actions and states an app depends on
- [Disconnect policy](docs/disconnect-policy.md) — what happens to in-flight work when the agent loses its connection
- [The agent journal](docs/journal.md) — the ordered, persisted record of everything an agent reports

## Development

```bash
uv sync                       # install dependencies
pytest -m "not integration"   # fast unit tests
pytest -m integration         # full integration tests (require Docker)
```

Integration tests spin up the rekuest stack with Docker Compose and exercise real
calls against it; see `tests/conftest.py`.

## Learn more

- 📚 [arkitekt.live](https://arkitekt.live)
- 🐙 [github.com/arkitektio/rekuest](https://github.com/arkitektio/rekuest)

## License

GPL-3.0-or-later
