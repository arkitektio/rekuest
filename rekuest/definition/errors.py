"""Errors related to definition handling"""

from rekuest.errors import RekuestError


class DefinitionError(RekuestError):
    """Base class for all definition errors"""

    pass


class NonSufficientDocumentation(DefinitionError):
    """Raised when we cannot infer sufficcient documentatoin"""
