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
    with use(x, auto_unreserve=False) as r:
        x = node.assign(*args, **kwargs)





def useUser():
    """Use the current User

    Returns:
        User: The current User
    """
    helper = get_current_assignation_helper()
    return helper.assignation.user
