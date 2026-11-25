"""The types rekuest sends by id: each expanded by the ``Rekuest`` client.

Imported by ``rekuest/__init__``, so each class is marked whenever rekuest is: an app
using ``Rekuest`` takes every one of them.
"""

from arkitekt_spec.actions import AssignWidgetInput
from arkitekt_spec.declare.structures.declare import structure
from arkitekt_spec.declare.widgets import SearchWidget

from rekuest.api.schema import (
    Action,
    Agent,
    DetailMaterializedBlok,
    Implementation,
    SearchActionsQuery,
    SearchAgentsQuery,
    SearchImplementationsQuery,
    SearchMaterializedBloksQuery,
    SearchShortcutsQuery,
    SearchTestCasesQuery,
    SearchTestResultsQuery,
    Shortcut,
    TaskEvent,
    TestCase,
    TestResult,
)
from rekuest.client.client import Rekuest


def _search(query: object) -> AssignWidgetInput:
    """The widget that picks one of these out of the deployment."""
    return SearchWidget(query=query.Meta.document, ward="rekuest")  # type: ignore[attr-defined]


@structure("@rekuest/implementation", widget=_search(SearchImplementationsQuery))
async def expand_implementation(id: str, rekuest: Rekuest) -> Implementation:
    """An implementation, by id."""
    return await rekuest.aget_implementation(id)


@structure("@rekuest/action", widget=_search(SearchActionsQuery))
async def expand_action(id: str, rekuest: Rekuest) -> Action:
    """An action, by id.

    ``find`` is a multi-argument finder -- ``find(id, implementation, hash,
    matching)``, all optional -- so the id is passed by name rather than relying
    on it happening to be first.
    """
    return await rekuest.afind(id=id)


@structure("@rekuest/shortcut", widget=_search(SearchShortcutsQuery))
async def expand_shortcut(id: str, rekuest: Rekuest) -> Shortcut:
    """A shortcut, by id."""
    return await rekuest.aget_shortcut(id)


@structure("@rekuest/testcase", widget=_search(SearchTestCasesQuery))
async def expand_test_case(id: str, rekuest: Rekuest) -> TestCase:
    """A test case, by id."""
    return await rekuest.aget_test_case(id)


@structure("@rekuest/testresult", widget=_search(SearchTestResultsQuery))
async def expand_test_result(id: str, rekuest: Rekuest) -> TestResult:
    """A test result, by id."""
    return await rekuest.aget_test_result(id)


@structure("@rekuest/materialized_blok", widget=_search(SearchMaterializedBloksQuery))
async def expand_materialized_blok(id: str, rekuest: Rekuest) -> DetailMaterializedBlok:
    """A materialized blok, by id, with the blok it shows: its tree, demo state and dependencies.

    The identifier is the one orkestrator already displays and links
    (``rekuest/manifest.ts``), so a message carrying one renders as the blok.
    """
    return await rekuest.aget_materialized_blok(id)


SUPERVISOR = "@rekuest/supervisor"
"""The descriptor of ``@rekuest/agent``: the app the agent says controls its lifecycle.

Self-declared (``ARKITEKT_SUPERVISOR``), so a supervisor's port narrows its picker with it
(``Requires(SUPERVISOR, DescriptorOperator.EQUALS, "plugin_engine")``) and still maps the
agent to its own records before acting: it is a hint, never authority."""


def describe_agent(agent: Agent) -> dict[str, str]:
    """An agent's descriptors: the supervisor it declared, if any.

    None declared is no descriptor at all, not ``None``: a constraint on a key the object
    does not carry is not tested, so an app that predates ``ARKITEKT_SUPERVISOR`` still
    reaches its supervisor's actions (which decide by their own records anyway).
    """
    return {SUPERVISOR: agent.supervisor} if agent.supervisor else {}


@structure("@rekuest/agent", widget=_search(SearchAgentsQuery), describe=describe_agent)
async def expand_agent(id: str, rekuest: Rekuest) -> Agent:
    """An agent, by id: an app instance connected to rekuest, with its lok client id."""
    return await rekuest.aget_agent(id)


@structure("@rekuest/taskevent")
async def expand_task_event(id: str, rekuest: Rekuest) -> TaskEvent:
    """A task event, by id. Its query is ``get_event``."""
    return await rekuest.aget_event(id)


__all__ = ["SUPERVISOR", "describe_agent"]
