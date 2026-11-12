# This file was generated with the assistance of an AI coding tool.
"""What the core asks of a database that holds models.

A backend gives simple steps, each in its database's own language: the model's record, the nodes, blocks and
references of a load, and what an export reads back. The algorithms stay in the core, so that every backend gives the
same answers. What is read back has today's form on Neo4j, whatever the backend stores: values as the records have
them, rows of points as rows of coordinates, and a reference as ``[type, position, STEP id]``.
"""

from __future__ import annotations

import abc
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar

from ifc_db.records import Block, NewEntity, Reference


class Backend(abc.ABC):
    """One database. ``name`` is what a server can announce, such as ``neo4j``."""

    name: ClassVar[str]

    # The model's record

    @abc.abstractmethod
    async def model(self, model_id: str) -> dict[str, Any] | None:
        """Every property of the model's record, or None when there is no such model."""

    @abc.abstractmethod
    async def models(self) -> list[dict[str, Any]]:
        """``{"id", "imported_at", "state"}`` of every model, in the order of the ids."""

    @abc.abstractmethod
    async def start_import(self, model_id: str, form_version: int) -> None:
        """Make the record if there is none, as a STEP model of this form, and take away ``imported_at``."""

    @abc.abstractmethod
    async def set_facts(self, model_id: str, facts: Mapping[str, Any]) -> None:
        """Add these values to the record, over those it has; a value of None takes its key away."""

    # A load

    @abc.abstractmethod
    async def add_entities(self, model_id: str, entities: Sequence[NewEntity]) -> None:
        """Add these instances as nodes; none of them is there yet."""

    @abc.abstractmethod
    async def add_blocks(self, model_id: str, blocks: Sequence[Block]) -> None:
        """Add these blocks to the model."""

    @abc.abstractmethod
    async def add_references(self, model_id: str, references: Sequence[Reference]) -> None:
        """Add these references between nodes that are there."""

    @abc.abstractmethod
    async def link_project(self, model_id: str) -> None:
        """Tie the record to the model's ``IfcProject``, if it has one."""

    # What an export reads back

    @abc.abstractmethod
    async def steps(self, model_id: str) -> list[int]:
        """The STEP ids of the model's nodes, in no given order."""

    @abc.abstractmethod
    async def entity_page(self, model_id: str, steps: Sequence[int]) -> list[tuple[int, dict[str, Any], list[Any]]]:
        """``(step, properties, references)`` for these nodes, in no given order, with the references that have a
        position, each as ``[type, position, STEP id]``."""

    @abc.abstractmethod
    async def block_sizes(self, model_id: str) -> dict[int, int]:
        """The number of instances in each of the model's blocks, by the block's first STEP id."""

    @abc.abstractmethod
    async def block_text(self, model_id: str, first: int) -> str:
        """The text of the block that begins at ``first``."""

    # Taking a model out

    @abc.abstractmethod
    async def delete_blocks(self, model_id: str) -> None:
        """Remove the model's blocks."""

    @abc.abstractmethod
    async def delete_entities(self, model_id: str) -> int:
        """Remove the model's nodes and their relationships. Returns how many nodes."""

    @abc.abstractmethod
    async def delete_record(self, model_id: str, *, keep: bool) -> None:
        """Remove the record of a STEP model, or, with ``keep``, only its ``imported_at``."""
