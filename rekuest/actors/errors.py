from rekuest.errors import RekuestError


class ActorException(RekuestError):
    pass


class UnknownMessageError(ActorException):
    pass


class ThreadedActorCancelled(ActorException):
    pass


class ContextError(ActorException):
    pass


class NotWithinAnAssignationError(ContextError):
    pass


class NotWithinAProvisionError(ContextError):
    pass
