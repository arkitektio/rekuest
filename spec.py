class DefinitionRegistry:
    """A class that holds all the definitions that are registered on it

    A definition is a function that is registered with a name and a description
    and can be used to create a actor
    """


class StructureRegistry:
    """A class that holds all the structures that are registered on it

    A structure is a class the is serializable and can be used to serialize
    inputs and outputs of a function
    """


# Arkitekt will use the apps exposed definition registry to create its own
# definition registry (nodes, templates) and then use that to call connected agents
# to provide their actors by sending them a provision message, this provision has a
# definition hash and a provision id, the provision id is used to identify the provision
# and the definition hash is used to identify the definition that should be provisioned
# (you can only ever have one function with a given definition on the same agent)
#
import uuid


class ProvisionStatus(Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    DENIED = "DENIED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


def get_flow():
    return "flow_logic"


class Provision:
    arkid: str  # This is the arkitekt id, it is used to UNIQUELY identify the provision on arkitekt
    id: str  # This is a manager string, that gets send by the actors direct supervisor (in memory)

    # If both arkid and id are equal, then the provision was created by arkitekt, otherwise
    # it was created by a local manager

    hash: str  # The hash is used to identify the definition that should be provisioned


class Assignation:
    arkid: str  # This is the arkitekt id, it is used to UNIQUELY identify the assignation on arkitekt
    id: str  # This is a manager string, that gets send by the actors direct supervisor (in memory)

    # if both arkid and id are equal, then the assignation was created by arkitekt, otherwise
    # it was created by a local manager

    args: List[Any]  # The args that are passed to the function


class AgentTransport:
    """The message protocol for the agent"""

    def update_provision(self, str, status: ProvisionStatus, message: str = ""):
        """Updates the status of a provision"""
        pass

    def aconnect(self):
        """Connects the transport to the server"""
        pass


class Definition:
    """A definition is a function that is registered with a name and a description,
    as well as a description of the underlying functionality"""

    name: str
    description: str

    def hash(self):
        """Returns a hash of the definition"""
        return "unique"


def reserve(provision: str) -> "Actor":
    """Reserves an actor from the definition registry"""
    return Actor(provision=provision)


class Agent:
    definition_registry: DefinitionRegistry
    transport: AgentTransport

    async def start(self):
        """This method is called when the agent starts."""
        # connect to transport and
        await self.transport.aconnect()
        pass

    async def process(self):
        """This method is called when the agent is processing messages"""
        pass


class RekuestRath:
    api: str

    def run(query):
        pass


class DefineMutation:
    pass


class Rekuest:
    rath: RekuestRath
    definition_registry: DefinitionRegistry
    agent: Agent

    async def run(self):
        """Runs the rekuest"""
        # This method will run the rekuest and return the result
        definitions = self.definition_registry.gather()

        # This will let arkitekt know which definitions are available
        self.rath.execute(DefineMutation, definitions)

        # This will let the agent wait for provisions that are sent by arkitekt
        self.agent.start()

        while True:
            await self.agent.process()  # process received messages

        pass


class Actor:
    """This class is supposed to connect to an agent or an actor and and manage itself."""

    structure_registry: StructureRegistry = None

    def on_provide(provision: str):
        pass

    def on_assignation(assignation: str, args: List[str]) -> List[str]:

        expand = expand(args, structure_registry=self.structure_registry)

        # run functiion=expand.function

        shrink(expand.result, structure_registry=self.structure_registry)

        return []

    def on_unassignation():
        pass


class ActorAgent:
    """This class is supposed to connect to an agent and manage itself the lifecycle of actors."""

    provision: Provision
    definition: Definition
    definition_registry: DefinitionRegistry = None

    managed_actors: Dict[str, Actor] = Field(default_factory=dict)

    def __init__(self) -> None:
        """The init function is created when the actor is created. This is synchronous and should not be used to do any async operations."""
        pass

    async def on_provide(self):
        """This method is called when the agent has created the actor and allows for asynchrnouns code to happen"""

        # eg you can do something like this
        logic = await get_flow(hash=self.definition.hash())

        # reserve all elements of the flow that are locally available
        # this will return a list of elements that are not locally available
        for element in logic:
            if element.is_local:
                unique_id = uuid.uuid4()
                transport = Transport(unique_id)
                self.managed_actors[unique_id] = element.reserve(
                    transport=transport, provision=provision
                )

        pass

    async def on_assignation(assignation: str, args: List[str]):
        """This method is called when the agent assigns an task to a provision."""

    async def on_unassignation(assignation):
        """This method is called when the agent unassigns a task from a provision."""

        pass

    async def on_unprovide(provision):
        """This method is called when the agent unprovides a provision."""

        pass
