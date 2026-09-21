"""The task an action is running for, handed to it by annotation.

    def segment(image: ArrayDataset, task: Task, mikro: Mikro):
        task.progress(10, "thresholding")
        mask = mikro.threshold(image)   # attributed to this task automatically
        ...

A ``Task`` is about the task only: what it is (id, user, org, token) and what
it reports (logs, progress, pause points, hooks). The app's clients are services
and are injected as their own parameters -- the one shared instance of each, not
a per-task copy. What attributes their requests to this task is
:data:`rath.task.current_task`, which the actor sets around the body. A task
knows no service: it speaks the agent protocol (:mod:`rekuest.messages`) and
nothing of any client, which is what lets ``arkitekt`` export it as its own.

The ``Task`` object itself looks nothing up, so it works the same in the loop, in
a worker thread and in code the action hands it to. The *ambient* task does not
reach a thread the action starts itself; pass ``task=`` to a call, or carry the
context, when you do that (see :mod:`rath.task`).
"""

import logging
from typing import TYPE_CHECKING, Any

from koil import unkoil

from rekuest.messages import LogLevel

if TYPE_CHECKING:
    from rekuest.actors.helper import AssignmentHelper
    from rekuest.actors.types import AssignmentHook
    from rekuest.messages import Assign

logger = logging.getLogger("rekuest.task")

_LOG_LEVELS = {
    LogLevel.DEBUG: logging.DEBUG,
    LogLevel.INFO: logging.INFO,
    LogLevel.WARN: logging.WARNING,
    LogLevel.ERROR: logging.ERROR,
    LogLevel.CRITICAL: logging.CRITICAL,
}

TASK_MARKER = "__rekuest_task__"
"""The class attribute that makes a parameter receive its :class:`Task`."""


class Task:
    """One running assignment, as the action that runs it sees it."""

    __rekuest_task__ = True

    def __init__(self, helper: "AssignmentHelper") -> None:
        self._helper = helper

    # -- what the task is ------------------------------------------------- #

    @property
    def assignment(self) -> "Assign":
        """The assignment this task runs."""
        return self._helper.assignment

    @property
    def id(self) -> str:
        """The task id."""
        return self._helper.task

    @property
    def user(self) -> str:
        """The user that caused the task."""
        return self._helper.user

    @property
    def org(self) -> str:
        """The organization the task runs in."""
        return self._helper.org

    @property
    def token(self) -> str | None:
        """The provenance token; ``None`` when the implementation opted out."""
        return self._helper.token

    @property
    def agent(self) -> Any:  # noqa: ANN401 - ActorContext
        """The agent running this task's actor; ``None`` for a :meth:`local` task.

        A client's calls are parented through it -- child calls go over the
        agent's socket -- and a dependency proxy is made for it.
        """
        return self._helper.agent

    # -- reporting -------------------------------------------------------- #

    async def alog(self, message: str, level: LogLevel = LogLevel.DEBUG) -> None:
        """Log to the task."""
        await self._helper.alog(level, str(message))

    def log(self, message: str, level: LogLevel = LogLevel.DEBUG) -> None:
        """Log to the task."""
        unkoil(self.alog, message, level)

    async def aprogress(self, percentage: int, message: str | None = None) -> None:
        """Report progress, 0-100."""
        await self._helper.aprogress(int(percentage), message=message)

    def progress(self, percentage: int, message: str | None = None) -> None:
        """Report progress, 0-100."""
        unkoil(self.aprogress, percentage, message)

    async def apausepoint(self) -> None:
        """Pause here if the task was asked to."""
        await self._helper.abreakpoint()

    def pausepoint(self) -> None:
        """Pause here if the task was asked to."""
        unkoil(self.apausepoint)

    def install_hook(self, hook: "AssignmentHook") -> None:
        """Install an assignment hook for this task."""
        self._helper.install_hook(hook)

    @classmethod
    def local(cls, id: str = "local", user: str = "local", org: str = "local") -> "Task":
        """Make a task for calling an action directly, with no agent behind it.

        ``segment(image, task=Task.local(), mikro=mikro)``: logs and progress go
        to the ``rekuest.task`` logger, pause points return at once, and there is
        no assignment and no provenance token, so clients handed out for it
        attribute nothing.

        Args:
            id: The task id its logs are tagged with.
            user: The user the task claims to run for.
            org: The organization the task claims to run in.

        Returns:
            The task.
        """
        return _LocalTask(_LocalHelper(id=id, user=user, org=org))  # type: ignore[arg-type]


class _LocalTask(Task):
    """A :class:`Task` whose sync reporting needs no event loop: there is no agent to reach."""

    def log(self, message: str, level: LogLevel = LogLevel.DEBUG) -> None:
        self._helper.log(level, str(message))

    def progress(self, percentage: int, message: str | None = None) -> None:
        self._helper.progress(int(percentage), message)

    def pausepoint(self) -> None:
        return None


class _LocalHelper:
    """What a :class:`Task` needs of a helper, for a task no agent is running."""

    assignment = None
    token = None
    agent = None

    def __init__(self, id: str, user: str, org: str) -> None:
        self.task = id
        self.user = user
        self.org = org
        self.hooks: list["AssignmentHook"] = []

    def log(self, level: LogLevel, message: str) -> None:
        logger.log(_LOG_LEVELS.get(level, logging.INFO), "[%s] %s", self.task, message)

    def progress(self, percentage: int, message: str | None = None) -> None:
        logger.info("[%s] %d%% %s", self.task, percentage, message or "")

    async def alog(self, level: LogLevel, message: str) -> None:
        self.log(level, message)

    async def aprogress(self, percentage: int, message: str | None = None) -> None:
        self.progress(percentage, message)

    async def abreakpoint(self) -> None:
        return None

    def install_hook(self, hook: "AssignmentHook") -> None:
        self.hooks.append(hook)


def is_task(obj: object) -> bool:
    """Whether ``obj`` (possibly ``Optional``/``Annotated``) is the Task class."""
    from rekuest.agents.types import unwrap_injectable

    cls = unwrap_injectable(obj)
    return isinstance(cls, type) and getattr(cls, TASK_MARKER, False) is True


__all__ = ["Task", "is_task", "TASK_MARKER"]
