"""Errors for hooks"""

from rekuest.agents.errors import AgentException


class HookError(AgentException):
    """
    Base class for all exceptions raised by a hook
    """


class StartupHookError(HookError):
    """
    Raised when a startup hook fails
    """


class ShutdownHookError(HookError):
    """
    Raised when a shutdown hook fails
    """
