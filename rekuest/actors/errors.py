"""Exceptions for the actors module."""

from rekuest.errors import RekuestError


class ActorException(RekuestError):
    """An exception that is raised by an actor"""

    pass


class UnknownMessageError(ActorException):
    """An exception that is raised when an actor receives an unknown message"""

    pass
