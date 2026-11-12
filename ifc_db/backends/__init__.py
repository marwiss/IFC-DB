# This file was generated with the assistance of an AI coding tool.
"""The databases a model can be kept in, each behind :class:`~ifc_db.backends.base.Backend`."""

from __future__ import annotations

from ifc_db.backends.base import Backend
from ifc_db.backends.cypher import CypherBackend
from ifc_db.graph import Graph


def as_backend(graph: Graph | Backend) -> Backend:
    """The backend for ``graph``: itself when it is one, and otherwise Cypher through its ``run``, as for Neo4j and
    Memgraph, so that every function that takes a graph today goes on taking one."""
    return graph if isinstance(graph, Backend) else CypherBackend(graph)
