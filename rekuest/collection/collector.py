from rekuest.actors.types import Passport, Assignment
from pydantic import BaseModel, Field
from typing import Dict, Any, List
import logging


logger = logging.getLogger(__name__)


class AssignationCollector(BaseModel):
    assignment: Assignment

    async def collect(self):
        """
        Collect data from the source.

        :return: The collected data.
        """
        raise NotImplementedError

    async def register(self, args: List[Any]):
        return


class ActorCollector(BaseModel):
    """
    The ActorCollector class is used to collect data in the course of a
    an anctors life time. It initiates Assignation that then in turn
    collect data during the Assignation liftetime
    """

    passport: Passport
    sub_collectors: Dict[str, AssignationCollector] = Field(default_factory=dict)
    delegated_collector: Dict[str, "ActorCollector"] = Field(default_factory=dict)

    def spawn(self, assignment: Assignment):
        """
        Spawn a new collector for the given assignation.
        """
        assign_collector = AssignationCollector(assignment=assignment)
        self.sub_collectors[assignment.parent] = assign_collector
        return assign_collector

    async def collect(self, assignment: Assignment):
        """
        Collect data from the source.

        :return: The collected data.
        """
        raise NotImplementedError


class Collector(BaseModel):
    """
    The Collector class is used to collect data in the course of a
    an agents life time. It initiates ActorCollectors that then in turn
    collect data during the actors liftetime
    """

    assignment_map: Dict[str, List[Any]] = Field(default_factory=dict)
    children_tree: Dict[str, List[str]] = Field(default_factory=dict)

    def register(self, assignment: Assignment, items: List[any]):
        logger.debug(f"Registering {assignment.id}")

        if assignment.id in self.assignment_map:
            self.assignment_map[assignment.id] += items
        else:
            self.assignment_map[assignment.id] = items
        if assignment.parent:
            if assignment.parent in self.children_tree:
                self.children_tree[assignment.parent].append(assignment.id)
            else:
                self.children_tree[assignment.parent] = [assignment.id]

    async def collect(self, id: str):
        """
        Collect data from the source.

        :return: The collected data.
        """

        if id in self.assignment_map:
            for v in self.assignment_map[id]:
                try:
                    await v.acollect()
                except:
                    logger.error("This shouldn't happend but lets see", exc_info=True)

        if id in self.children_tree:
            for child in self.children_tree[id]:
                await self.collect(child)

    class Config:
        copy_on_model_validation = False
