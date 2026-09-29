# Workflows

A workflow is an action that calls other actions. It is declared with `@app.workflow`
instead of `@app.action` and called exactly like any action: from the UI, from a script,
or from another workflow. What sets it apart is what happens when an agent dies.

```python
from typing import AsyncGenerator, Protocol

from arkitekt import AgentLost, App, Task, run

app = App("counter-flow", "0.1.0")


@app.declare(app="counter", auto_resolvable=True, min=1)
class Counter(Protocol):
    async def count(self, to: int) -> AsyncGenerator[str, None]:
        """Count"""
        ...

    def add(self, a: int, b: int) -> int:
        """Add"""
        ...


@app.workflow
async def count_and_add(counter: Counter, to: int, *, task: Task) -> AsyncGenerator[str, None]:
    """Count And Add"""
    async for step in counter.count(to=to):  # a generator method streams every yield
        yield step
    total = await task.aretry(counter.add.acall, to, to, attempts=3, if_started=True)
    yield f"total: {total}"


if __name__ == "__main__":
    run(app)
```

## Only a workflow may call other actions

A plain action is refused at registration if it depends on another app's actions, and a
call made from inside one raises `NotAWorkflowError`. The rule exists because only a
workflow can be resumed: a plain action that called three others and then died would
have no way to find those calls again.

A dependency that only names another agent's states is fine on a plain action.

## When an agent dies

| What died | Who handles it | How |
|---|---|---|
| the agent of a **plain** task | whoever called | the task ends **LOST**; the call raises `AgentLost` |
| the agent of a **workflow** | the server | the workflow is **resumed** |
| a **step** inside a workflow | the workflow's code | the step's call raises `AgentLost` |

The server re-runs nothing on its own, except work that was never picked up: that is
delivered again.

**LOST** is terminal: not failed, the fate is unknown. It is final, too: if the dead
agent's outcome turns up later, it is kept as a `LATE_REPORT` event, but the task
stays LOST.

`AgentLost` carries what is known:

- `started`: whether the task was ever picked up. If not, nothing ran, and sending it
  again is always safe.
- `last_progress`: the last progress it reported.
- `effects`: the action's claim about what running it again would do.

## Resume

A resumed workflow runs again from the top, in a new process, but it does not redo
anything:

- each call it already made is found again, by a key derived from the target, the
  arguments and how often the workflow made that call. A finished call returns its
  recorded result (a stream replays its yields); a call still running is followed on;
- `task.now()`, `task.random()`, `task.sleep()` and `task.record(fn)` return their
  recorded values (a resumed `sleep` only waits out what was left).

In exchange, **the code must be deterministic**. Outside values come in only through
calls and those `task` helpers, because they are what gets recorded. Wrap anything else
that varies between runs (an LLM completion, a search, reading a file) in
`task.record(fn)`: its value must be JSON. A resumed run that takes a different path
raises `NonDeterministicWorkflow`.

Pass `call_key=` yourself when two concurrent calls to the same target with the same
arguments must be told apart (a flow engine names them by node).

A workflow is resumed only by the code it ran: if its source changed in between, or its
agent died three times, it ends LOST instead. Only the workflow function's own source is
compared, so an edit to a helper it calls is not noticed.

## Handling a lost step

```python
try:
    handler.dispense(well, volume_ul=v)
except AgentLost as lost:
    if not lost.started:          # never reached the robot: nothing happened
        handler.dispense(well, volume_ul=v)
    else:                         # started, fate unknown: a person decides
        task.hold(f"dispense into {well} was lost: check the well", lost=lost)
```

- **`task.retry(call, *args, attempts=3, if_started=False)`** calls again on `AgentLost`,
  but only for a step that never started, unless `if_started=True` says a repeat is
  fine. Other exceptions are never retried.
- **`task.hold(message, lost=...)`** pauses the workflow with your message (and the lost
  step's effects and progress) until a person resumes it, when it carries on, or cancels
  it. A hold that was resumed is recorded, so a resumed run does not hold there again.
- **`with task.guard(dep.state, "path")`** records the revision of another agent's state.
  On a resume, the block raises `StateChanged` if a guarded path was changed by
  anything but this workflow's own calls, or that agent restarted. Nothing is locked
  while the workflow is down; the change is noticed when it comes back.

## `effects=`

```python
@app.action(effects=Effects.NONE)
def count_cells(image: Image) -> int: ...

app = App("liquid-handler", "0.1.0", effects=Effects.IRREVERSIBLE)  # the app's default
```

| `Effects` | Running it again… |
|---|---|
| `NONE` | changes nothing |
| `REPEATABLE` | leaves the same state as running it once |
| `UNKNOWN` (the default) | nobody has said |
| `IRREVERSIBLE` | happens again, in the real world |

It is **information, never a rule**: it rides on `AgentLost` and on a hold so that
whoever decides sees it. Neither the server nor `task.retry` acts on it.

## Known gaps

- A resumed run reports its progress, logs and a workflow's own yields again; they
  show up twice in its history.
- A guard sees only what the other app models as state.
