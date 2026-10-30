"""The socket control plane: what ``Init`` told us.

Registration itself is the transport handshake; what the control plane owns is the
bookkeeping ``Init`` carries. Shelving is the agent's own (numbered frames nothing
answers).
"""



from arkitekt_runtime import messages
from rekuest.agents.control import SocketControlPlane

from arkitekt_runtime.local import MemoryAgentTransport


def test_handle_init_records_the_agent_and_its_hash() -> None:
    plane = SocketControlPlane(MemoryAgentTransport())
    assert not plane.acknowledged and plane.agent_id is None

    plane.handle_init(messages.Init(agent="agent-1", hash="h1"))

    assert plane.acknowledged
    assert plane.agent_id == "agent-1" and plane.server_hash == "h1"
