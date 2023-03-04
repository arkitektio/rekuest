from typing import Any
from rekuest.postmans.utils import use
from rekuest.traits.node import Reserve
from rekuest.actors.vars import get_current_assignation_helper


def assign(node: Reserve, *args, **kwargs) -> Any:
    """Assign a task to a Node

    Args:
        node (Reserve): Node to assign to
        args (tuple): Arguments to pass to the node
        kwargs (dict): Keyword arguments to pass to the node
    Returns:
        Any: Result of the node task

    """
    with use(node, auto_unreserve=False):
        return node.assign(*args, **kwargs)


def useUser() -> str:
    """Use the current User

    Returns:
        User: The current User
    """
    helper = get_current_assignation_helper()
    return helper.assignation.user
