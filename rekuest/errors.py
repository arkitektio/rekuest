"""Custom exceptions for Rekuest."""


class RekuestError(Exception):
    """Base class for all Rekuest exceptions."""

    pass


class NoRekuestRathFoundError(RekuestError):
    """Raised when no Rekuest Rathfound is found."""

    pass


class CriticalCallError(RekuestError):
    """Raised when a critical error occurs during a remote call."""

    pass


class ErrorCallError(RekuestError):
    """Raised when an error occurs during a remote call."""

    pass


class NoRegistryError(RekuestError):
    """Raised when something asks to be registered but names no registry.

    Registration goes through an app: there is no process-wide registry to fall
    back to, on purpose -- it made what an app ran depend on what the process had
    imported.
    """

    @classmethod
    def for_decorator(
        cls, what: str, decorator: str, example: str, parameter: str
    ) -> "NoRegistryError":
        """The error for a decorator used with no registry, showing the app-based form.

        Args:
            what: What was being registered, as the subject of "goes through an app".
            decorator: The app method to use instead (``"register"``, ``"state"``, ...).
            example: The decorated definition the example shows.
            parameter: The keyword that registers into a registry directly.
        """
        return cls(
            f"There is no registry to register into. {what} through an app:\n\n"
            f'    app = App(identifier="my-app")\n\n    @app.{decorator}\n    {example}\n\n'
            f"Pass `{parameter}=` to register into a registry directly."
        )


class AppContextError(RekuestError):
    """A run's app context does not fit what the app declared.

    An app declares at most one app-context class; a run must then pass an
    instance of it (``run(app, context=...)``), and a run of an app that
    declares none must pass nothing. Raised before the agent starts, so the
    mismatch never reaches a hook or an action.
    """


class RegistryFrozenError(RekuestError):
    """Raised when something is registered on an app that is already running.

    An app is configured, then entered. What it offers is fixed at the moment it
    connects, because that is what it told the server; registering afterwards would
    add something the server never heard about and no caller can reach.
    """
