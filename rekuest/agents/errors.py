class AgentException(Exception):
    """
    Base class for all exceptions raised by the Agent.
    """


class ProvisionException(AgentException):
    """
    Base class for all exceptions raised by the Agent.
    """


class HookError(AgentException):
    """
    Base class for all exceptions raised by a hook
    """


class StartupHookError(HookError):
    """
    Raised when a startup hook fails
    """


class BackgroundHookError(HookError):
    """
    Raised when a background hook fails
    """
