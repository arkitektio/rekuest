import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Awaitable, Callable, Optional
from koil.helpers import iterate_spawned, run_spawned
from pydantic import BaseModel, Field
from rekuest.actors.base import Actor
from rekuest.actors.helper import AsyncAssignationHelper, ThreadedAssignationHelper
from rekuest.actors.vars import current_assignation_helper
from rekuest.api.schema import AssignationStatus, ProvisionFragment
from rekuest.messages import Assignation, Provision
from rekuest.structures.serialization.actor import expand_inputs, shrink_outputs


logger = logging.getLogger(__name__)


class FunctionalActor(BaseModel):
    assign: Callable[..., Any]
    provide: Optional[Callable[[ProvisionFragment], Awaitable[Any]]]
    unprovide: Optional[Callable[[], Awaitable[Any]]]

    async def on_provide(self, assignation: ProvisionFragment):
        return None if not self.provide else await self.provide(assignation)

    async def on_unprovide(self):
        return None if not self.unprovide else await self.unprovide()

    class Config:
        arbitrary_types_allowed = True


class AsyncFuncActor(Actor):
    async def on_assign(self, assignation: Assignation):
        logging.info("Assigning %s", assignation)
        try:
            params = await expand_inputs(
                self.provision.template.node,
                assignation.args,
                structure_registry=self.structure_registry,
                skip_expanding=not self.expand_inputs,
            )

            await self.transport.change_assignation(
                assignation.assignation,
                status=AssignationStatus.ASSIGNED,
            )

            current_assignation_helper.set(
                AsyncAssignationHelper(
                    actor=self, assignation=assignation, provision=self.provision
                )
            )

            returns = await self.assign(**params)

            current_assignation_helper.set(None)

            returns = await shrink_outputs(
                self.provision.template.node,
                returns,
                structure_registry=self.structure_registry,
                skip_shrinking=not self.shrink_outputs,
            )

            await self.transport.change_assignation(
                assignation.assignation,
                status=AssignationStatus.RETURNED,
                returns=returns,
            )

        except asyncio.CancelledError:

            await self.transport.change_assignation(
                assignation.assignation, status=AssignationStatus.CANCELLED
            )

        except AssertionError as ex:
            await self.transport.change_assignation(
                assignation.assignation,
                status=AssignationStatus.ERROR,
                message=str(ex),
            )

        except Exception as e:
            logger.exception(e)
            await self.transport.change_assignation(
                assignation.assignation,
                status=AssignationStatus.CRITICAL,
                message=repr(e),
            )


class AsyncGenActor(Actor):
    async def on_assign(self, assignation: Assignation):
        try:
            params = await expand_inputs(
                self.provision.template.node,
                assignation.args,
                structure_registry=self.structure_registry,
                skip_expanding=not self.expand_inputs,
            )

            current_assignation_helper.set(
                AsyncAssignationHelper(
                    actor=self, assignation=assignation, provision=self.provision
                )
            )

            await self.transport.change_assignation(
                assignation.assignation,
                status=AssignationStatus.ASSIGNED,
            )

            async for returns in self.assign(**params):

                returns = await shrink_outputs(
                    self.provision.template.node,
                    returns,
                    structure_registry=self.structure_registry,
                    skip_shrinking=not self.shrink_outputs,
                )

                await self.transport.change_assignation(
                    assignation.assignation,
                    status=AssignationStatus.YIELD,
                    returns=returns,
                )

            current_assignation_helper.set(None)

            await self.transport.change_assignation(
                assignation.assignation, status=AssignationStatus.DONE
            )

        except asyncio.CancelledError:

            await self.transport.change_assignation(
                assignation.assignation, status=AssignationStatus.CANCELLED
            )

        except AssertionError as ex:
            await self.transport.change_assignation(
                assignation.assignation,
                status=AssignationStatus.ERROR,
                message=str(ex),
            )

        except Exception as ex:
            logger.error("Error in actor", exc_info=True)
            await self.transport.change_assignation(
                assignation.assignation,
                status=AssignationStatus.CRITICAL,
                message=str(ex),
            )

            raise ex


class FunctionalFuncActor(FunctionalActor, AsyncFuncActor):
    async def progress(self, value, percentage):
        await self._progress(value, percentage)

    class Config:
        arbitrary_types_allowed = True


class FunctionalGenActor(FunctionalActor, AsyncGenActor):
    async def progress(self, value, percentage):
        await self._progress(value, percentage)

    class Config:
        arbitrary_types_allowed = True


class ThreadedFuncActor(Actor):
    executor: ThreadPoolExecutor = Field(default_factory=lambda: ThreadPoolExecutor(4))

    async def on_assign(self, assignation: Assignation):

        try:
            logger.info("Assigning Number two")
            params = await expand_inputs(
                self.provision.template.node,
                assignation.args,
                structure_registry=self.structure_registry,
                skip_expanding=not self.expand_inputs,
            )

            await self.transport.change_assignation(
                assignation.assignation,
                status=AssignationStatus.ASSIGNED,
            )

            current_assignation_helper.set(
                ThreadedAssignationHelper(
                    actor=self, assignation=assignation, provision=self.provision
                )
            )

            returns = await run_spawned(
                self.assign, **params, executor=self.executor, pass_context=True
            )

            current_assignation_helper.set(None)
            returns = await shrink_outputs(
                self.provision.template.node,
                returns,
                structure_registry=self.structure_registry,
                skip_shrinking=not self.shrink_outputs,
            )

            await self.transport.change_assignation(
                assignation.assignation,
                status=AssignationStatus.RETURNED,
                returns=returns,
            )

        except asyncio.CancelledError as e:
            logger.info("Actor Cancelled")

            await self.transport.change_assignation(
                assignation.assignation,
                status=AssignationStatus.CANCELLED,
                message=str(e),
            )

        except AssertionError as ex:
            await self.transport.change_assignation(
                assignation.assignation,
                status=AssignationStatus.ERROR,
                message=str(ex),
            )

        except Exception as e:
            logger.error("Error in actor", exc_info=True)
            await self.transport.change_assignation(
                assignation.assignation,
                status=AssignationStatus.CRITICAL,
                message=str(e),
            )


class CompletlyThreadedActor(ThreadedFuncActor):
    executor: ThreadPoolExecutor = Field(default_factory=lambda: ThreadPoolExecutor(4))

    def provide(self, provision: ProvisionFragment):
        return None

    def unprovide(self):
        return None

    async def on_provide(self, *args, **kwargs):
        return await run_spawned(
            self.provide, *args, **kwargs, executor=self.executor, pass_context=True
        )

    async def on_unprovide(self, *args, **kwargs):
        return await run_spawned(
            self.unprovide, *args, **kwargs, executor=self.executor, pass_context=True
        )

    async def on_assign(self, assignation: Assignation):

        try:
            logger.info("Assigning Number two")
            params = await expand_inputs(
                self.provision.template.node,
                assignation.args,
                structure_registry=self.structure_registry,
                skip_expanding=not self.expand_inputs,
            )

            await self.transport.change_assignation(
                assignation.assignation,
                status=AssignationStatus.ASSIGNED,
            )

            current_assignation_helper.set(
                ThreadedAssignationHelper(
                    actor=self, assignation=assignation, provision=self.provision
                )
            )

            returns = await run_spawned(
                self.assign, **params, executor=self.executor, pass_context=True
            )

            current_assignation_helper.set(None)
            returns = await shrink_outputs(
                self.provision.template.node,
                returns,
                structure_registry=self.structure_registry,
                skip_shrinking=not self.shrink_outputs,
            )

            await self.transport.change_assignation(
                assignation.assignation,
                status=AssignationStatus.RETURNED,
                returns=returns,
            )

        except asyncio.CancelledError as e:
            logger.info("Actor Cancelled")

            await self.transport.change_assignation(
                assignation.assignation,
                status=AssignationStatus.CANCELLED,
                message=str(e),
            )

        except AssertionError as ex:
            await self.transport.change_assignation(
                assignation.assignation,
                status=AssignationStatus.ERROR,
                message=str(ex),
            )

        except Exception as e:
            logger.error("Error in actor", exc_info=True)
            await self.transport.change_assignation(
                assignation.assignation,
                status=AssignationStatus.CRITICAL,
                message=str(e),
            )


class ThreadedGenActor(Actor):
    executor: ThreadPoolExecutor = Field(default_factory=lambda: ThreadPoolExecutor(4))

    async def on_assign(self, assignation: Assignation):
        try:
            params = await expand_inputs(
                self.provision.template.node,
                assignation.args,
                structure_registry=self.structure_registry,
                skip_expanding=not self.expand_inputs,
            )
            current_assignation_helper.set(
                ThreadedAssignationHelper(
                    actor=self, assignation=assignation, provision=self.provision
                )
            )

            await self.transport.change_assignation(
                assignation.assignation,
                status=AssignationStatus.ASSIGNED,
            )

            async for returns in iterate_spawned(
                self.assign, **params, executor=self.executor, pass_context=True
            ):

                returns = await shrink_outputs(
                    self.provision.template.node,
                    returns,
                    structure_registry=self.structure_registry,
                    skip_shrinking=not self.shrink_outputs,
                )

                await self.transport.change_assignation(
                    assignation.assignation,
                    status=AssignationStatus.YIELD,
                    returns=returns,
                )

            current_assignation_helper.set(None)

            await self.transport.change_assignation(
                assignation.assignation, status=AssignationStatus.DONE
            )

        except asyncio.CancelledError as e:

            await self.transport.change_assignation(
                assignation.assignation,
                status=AssignationStatus.CANCELLED,
                message=str(e),
            )

        except AssertionError as ex:
            await self.transport.change_assignation(
                assignation.assignation,
                status=AssignationStatus.ERROR,
                message=str(ex),
            )

        except Exception as e:
            logger.error("Error in actor", exc_info=True)
            await self.transport.change_assignation(
                assignation.assignation,
                status=AssignationStatus.CRITICAL,
                message=str(e),
            )

            raise e


class FunctionalThreadedFuncActor(FunctionalActor, ThreadedFuncActor):
    async def progress(self, value, percentage):
        await self._progress(value, percentage)

    class Config:
        arbitrary_types_allowed = True


class FunctionalThreadedGenActor(FunctionalActor, ThreadedGenActor):
    async def progress(self, value, percentage):
        await self._progress(value, percentage)

    class Config:
        arbitrary_types_allowed = True
