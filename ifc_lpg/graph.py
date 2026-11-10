# This file was generated with the assistance of an AI coding tool.
"""What the importer and the exporter need of a graph database, and a way to give it to them.

The importer and the exporter only run parameterised Cypher and read rows back, so a graph is anything with an async
``run(query, **params)`` that returns the rows as dicts: :class:`Graph`. :class:`Neo4jGraph` is that over the official
``neo4j`` driver (the ``neo4j`` extra), which speaks to Neo4j and to Memgraph alike. :func:`ensure_schema` makes the
constraints and indexes the queries rely on, in either database's words. The Cypher is portable: no APOC, and no
``elementId()`` or ``id()`` as keys.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Literal, Protocol

Dialect = Literal["neo4j", "memgraph"]

UNIQUE = (("Model", "id"), ("Entity", "id"), ("StepBlock", "id"), ("Object", "id"))
INDEXED = (("Entity", "GlobalId"), ("Entity", "model_id"), ("Object", "model_id"))
POINT_INDEXED = (("Entity", "bbox_centre"),)  # the spatial layer, ifc_lpg.spatial
FORM_VERSION = 1  # the version of the graph's shape on every Model, raised when the shape changes
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_ALREADY = ("already exists", "alreadyexists", "equivalentschemarulealreadyexists", "constraintalreadyexists")


class Graph(Protocol):
    """A graph database to the importer and the exporter: parameterised Cypher in, rows out."""

    async def run(self, query: str, /, **params: Any) -> list[dict[str, Any]]: ...


class Neo4jGraph:
    """A :class:`Graph` over the official async driver (``neo4j.AsyncGraphDatabase.driver``), for Neo4j and Memgraph."""

    def __init__(self, driver: Any, database: str | None = None) -> None:
        self.driver = driver
        self.database = database

    async def run(self, query: str, /, **params: Any) -> list[dict[str, Any]]:
        records, _summary, _keys = await self.driver.execute_query(query, parameters_=params, database_=self.database)
        return [record.data() for record in records]

    async def ddl(self, statement: str) -> None:
        """A schema statement outside any transaction, which Memgraph asks for. One that exists already is no error,
        and a transient error, as when two processes change the schema at once, is tried again."""
        for attempt in range(5):
            try:
                async with self.driver.session(database=self.database) as session:
                    await (await session.run(statement)).consume()
                return
            except Exception as exc:
                message = str(exc).lower()
                if any(marker in message for marker in _ALREADY):
                    return
                if "transient" not in type(exc).__name__.lower() or attempt == 4:
                    raise
                await asyncio.sleep(0.2 * (attempt + 1))


def schema_statements(dialect: Dialect) -> list[str]:
    """The constraints and indexes, in the dialect's words. Neo4j backs a unique constraint with an index; Memgraph does
    not, so it gets one beside it."""
    statements: list[str] = []
    for label, prop in UNIQUE:
        _check(label, prop)
        if dialect == "neo4j":
            statements.append(
                f"CREATE CONSTRAINT {label}_{prop}_unique IF NOT EXISTS FOR (n:{label}) REQUIRE n.{prop} IS UNIQUE"
            )
        else:
            statements += [f"CREATE CONSTRAINT ON (n:{label}) ASSERT n.{prop} IS UNIQUE", _index(dialect, label, prop)]
    statements += [_index(dialect, label, prop) for label, prop in INDEXED]
    statements += [_point_index(dialect, label, prop) for label, prop in POINT_INDEXED]
    return statements


def _point_index(dialect: Dialect, label: str, prop: str) -> str:
    _check(label, prop)
    if dialect == "neo4j":
        return f"CREATE POINT INDEX {label}_{prop}_point IF NOT EXISTS FOR (n:{label}) ON (n.{prop})"
    return f"CREATE POINT INDEX ON :{label}({prop})"


async def rebuild_point_indexes(graph: Neo4jGraph, dialect: Dialect) -> None:
    """Make the point indexes anew, after points have been set. Memgraph 3.14 does not keep a point index up to date:
    a point set after the index was made is not found through it, so the index is dropped and made again there. Neo4j
    keeps its indexes up to date, so nothing is done."""
    if dialect != "memgraph":
        return
    for label, prop in POINT_INDEXED:
        _check(label, prop)
        try:
            await graph.ddl(f"DROP POINT INDEX ON :{label}({prop})")
        except Exception as exc:
            if "not exist" not in str(exc).lower() and "doesn't exist" not in str(exc).lower():
                raise
        await graph.ddl(_point_index(dialect, label, prop))


def _index(dialect: Dialect, label: str, prop: str) -> str:
    _check(label, prop)
    if dialect == "neo4j":
        return f"CREATE INDEX {label}_{prop}_index IF NOT EXISTS FOR (n:{label}) ON (n.{prop})"
    return f"CREATE INDEX ON :{label}({prop})"


def _check(*names: str) -> None:
    for name in names:
        if not _NAME.match(name):
            raise ValueError(f"not an identifier: {name!r}")


async def ensure_schema(graph: Neo4jGraph, dialect: Dialect) -> None:
    """Make the constraints and indexes that the importer's and the exporter's queries rely on."""
    for statement in schema_statements(dialect):
        await graph.ddl(statement)
