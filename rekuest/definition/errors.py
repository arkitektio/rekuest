from rekuest.errors import RekuestError


class DefinitionError(RekuestError):
    """Base class for all definition errors"""

    pass


class NoDefinitionRegistryFound(RekuestError):
    """Raised when no definition registry is found"""

    pass
