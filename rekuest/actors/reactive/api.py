from rekuest.actors.vars import (
    get_current_assignation_helper,
    get_current_provision_helper,
)
from rekuest.api.schema import LogLevelInput
from rekuest.actors.vars import (
    get_current_assignation_helper,
    get_current_provision_helper,
)


async def alog(message: str, level: LogLevelInput = LogLevelInput.DEBUG) -> None:
    await get_current_assignation_helper().alog(level, message)


async def plog(message: str, level: LogLevelInput = LogLevelInput.DEBUG) -> None:
    await get_current_provision_helper().alog(level, message)


def useUser() -> str:
    """Returns the user id of the current assignation"""
    return get_current_assignation_helper().user


def useGuardian() -> str:
    """Returns the guardian id of the current provision"""
    return get_current_provision_helper().guardian


def progress(percentage: int) -> None:
    """Progress

    Args:
        percentage (int): Percentage to progress to
    """

    helper = get_current_assignation_helper()
    helper.progress(percentage)


async def aprogress(percentage: int) -> None:
    """Progress

    Args:
        percentage (int): Percentage to progress to
    """

    helper = get_current_assignation_helper()
    await helper.aprogress(percentage)
