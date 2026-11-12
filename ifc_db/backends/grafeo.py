# This file was generated with the assistance of an AI coding tool.
"""Grafeo: a model in GQL (ISO/IEC 39075), over Bolt with Neo4j's driver.

Grafeo's server reads every statement that comes over Bolt as GQL, so the official driver (the ``neo4j`` extra) is all
it takes. The graph has Neo4j's shape: ``Entity`` nodes with the class and its supertypes as labels, references as
relationships with their ``position``, blocks as ``StepBlock`` nodes, and the record as a ``Model``. Grafeo has no
points, so a row of points is a list of lists of numbers, which is what an export gives for Neo4j's points too.

Every value goes as a parameter, numbers above all: a literal with a decimal point is an exact DECIMAL in GQL. Labels
and relationship types are checked as identifiers and quoted. The schema is constraints and indexes in the default
graph, with no graph type: a graph type that names a node type takes the type out of the default graph (Grafeo's #730
and #764).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, ClassVar

from ifc_db.backends.base import Backend
from ifc_db.graph import INDEXED, UNIQUE
from ifc_db.records import BATCH, Block, NewEntity, Reference
from ifc_db.schema import safe_identifier

BLOCK_BATCH = 100  # blocks are deleted a few at a time, for each holds a lot of text


def _name(name: str) -> str:
    return f"`{safe_identifier(name)}`"


def schema_statements() -> list[str]:
    """The constraints and indexes the statements look nodes up by. A unique constraint makes an index of its own."""
    statements = [
        f"CREATE CONSTRAINT IF NOT EXISTS {_name(f'{label}_{prop}_unique')} FOR (n:{_name(label)}) "
        f"ON (n.{_name(prop)}) UNIQUE"
        for label, prop in UNIQUE
    ]
    statements += [
        f"CREATE INDEX IF NOT EXISTS {_name(f'{label}_{prop}_index')} FOR (n:{_name(label)}) ON (n.{_name(prop)})"
        for label, prop in INDEXED
    ]
    return statements


class GrafeoBackend(Backend):
    """A Grafeo server, through the official async driver (``neo4j.AsyncGraphDatabase.driver``)."""

    name: ClassVar[str] = "grafeo"

    def __init__(self, driver: Any, database: str | None = None) -> None:
        self.driver = driver
        self.database = database

    async def run(self, statement: str, /, **params: Any) -> list[dict[str, Any]]:
        """A GQL statement in a transaction of its own, with its values as parameters. Returns the rows as dicts."""
        records, _summary, _keys = await self.driver.execute_query(
            statement, parameters_=params, database_=self.database
        )
        return [record.data() for record in records]

    async def ensure_schema(self) -> None:
        """Make the constraints and indexes that are not there yet."""
        for statement in schema_statements():
            await self.run(statement)

    async def counts(self, model_id: str) -> dict[str, int]:
        """What the database holds of a model: its nodes, the relationships between them, its blocks and the instances
        in those. Grafeo has taken statements it did not carry out without a word, so a load is counted after."""
        entities = await self.run("MATCH (n:Entity {model_id: $id}) RETURN count(n) AS n", id=model_id)
        relationships = await self.run(
            "MATCH (:Entity {model_id: $id})-[r]->(:Entity) RETURN count(r) AS n", id=model_id
        )
        sizes = await self.block_sizes(model_id)
        return {
            "entities": entities[0]["n"],
            "relationships": relationships[0]["n"],
            "blocks": len(sizes),
            "in_blocks": sum(sizes.values()),
        }

    # The model's record

    async def model(self, model_id: str) -> dict[str, Any] | None:
        rows = await self.run("MATCH (m:Model {id: $id}) RETURN properties(m) AS properties", id=model_id)
        return rows[0]["properties"] if rows else None

    async def models(self) -> list[dict[str, Any]]:
        return await self.run(
            "MATCH (m:Model) RETURN m.id AS id, m.imported_at AS imported_at, m.state AS state ORDER BY id"
        )

    async def start_import(self, model_id: str, form_version: int) -> None:
        await self.run(
            "MERGE (m:Model {id: $id}) SET m += $facts REMOVE m.imported_at",
            id=model_id,
            facts={"format": "step", "form_version": form_version},
        )

    async def set_facts(self, model_id: str, facts: Mapping[str, Any]) -> None:
        await self._update(model_id, facts, ())

    async def _update(self, model_id: str, facts: Mapping[str, Any], remove: Iterable[str]) -> None:
        """Set ``facts`` on the record and take ``remove`` away. A fact that is None is taken away too, as Neo4j
        does with ``SET m += $facts``, so that the record has the same keys in both."""
        gone = sorted({*remove, *(key for key, value in facts.items() if value is None)})
        kept = {key: value for key, value in facts.items() if value is not None}
        removal = f" REMOVE {', '.join(f'm.{_name(key)}' for key in gone)}" if gone else ""
        await self.run(f"MATCH (m:Model {{id: $id}}) SET m += $facts{removal}", id=model_id, facts=kept)

    # A load

    async def add_entities(self, model_id: str, entities: Sequence[NewEntity]) -> None:
        """One statement for each set of labels. A row's values are its properties, its rows of points and its keys."""
        groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
        for entity in entities:
            keys = {"id": f"{model_id}:{entity.step}", "step_id": entity.step, "model_id": model_id}
            groups.setdefault(entity.labels, []).append({**entity.props, **entity.points, **keys})
        for labels, rows in groups.items():
            label_set = "&".join(_name(label) for label in ("Entity", *labels))
            await self.run(f"FOR row IN $rows INSERT (n:{label_set}) SET n += row", rows=rows)

    async def add_blocks(self, model_id: str, blocks: Sequence[Block]) -> None:
        if blocks:
            await self.run(
                """
                MATCH (m:Model {id: $model_id})
                FOR row IN $rows
                INSERT (m)-[:HAS_STEP_BLOCK]->(:StepBlock {
                    id: row.id, first_step: row.first, instances: row.instances, text: row.text
                })
                """,
                model_id=model_id,
                rows=[
                    {
                        "id": f"{model_id}:{block.first}",
                        "first": block.first,
                        "instances": block.instances,
                        "text": block.text,
                    }
                    for block in blocks
                ],
            )

    async def add_references(self, model_id: str, references: Sequence[Reference]) -> None:
        """One statement for each type of relationship, with its position or, for an inverse one, without."""
        groups: dict[tuple[str, bool], list[dict[str, Any]]] = {}
        for reference in references:
            row = {"a": f"{model_id}:{reference.a}", "b": f"{model_id}:{reference.b}", "position": reference.position}
            groups.setdefault((reference.type, reference.position is not None), []).append(row)
        for (kind, positioned), rows in groups.items():
            position = " {position: row.position}" if positioned else ""
            await self.run(
                "FOR row IN $rows MATCH (a:Entity {id: row.a}), (b:Entity {id: row.b}) "
                f"INSERT (a)-[:{_name(kind)}{position}]->(b)",
                rows=rows,
            )

    async def link_project(self, model_id: str) -> None:
        await self.run(
            """
            MATCH (m:Model {id: $id}), (p:Entity&IfcProject {model_id: $id})
            WHERE NOT EXISTS { MATCH (m)-[:HAS_PROJECT]->(p) }
            INSERT (m)-[:HAS_PROJECT]->(p)
            """,
            id=model_id,
        )

    # What an export reads back

    async def steps(self, model_id: str) -> list[int]:
        rows = await self.run("MATCH (n:Entity {model_id: $id}) RETURN n.step_id AS step", id=model_id)
        return [row["step"] for row in rows]

    async def entity_page(self, model_id: str, steps: Sequence[int]) -> list[tuple[int, dict[str, Any], list[Any]]]:
        """The nodes and their references in two statements, put together here."""
        ids = [f"{model_id}:{step}" for step in steps]
        nodes = await self.run(
            "FOR node_id IN $ids MATCH (n:Entity {id: node_id}) RETURN n.step_id AS step, properties(n) AS props",
            ids=ids,
        )
        found = await self.run(
            """
            FOR node_id IN $ids
            MATCH (n:Entity {id: node_id})-[r]->(t:Entity)
            WHERE r.position IS NOT NULL
            RETURN n.step_id AS step, type(r) AS kind, r.position AS position, t.step_id AS target
            """,
            ids=ids,
        )
        references: dict[int, list[Any]] = {}
        for row in found:
            references.setdefault(row["step"], []).append([row["kind"], row["position"], row["target"]])
        return [(row["step"], row["props"], references.get(row["step"], [])) for row in nodes]

    async def block_sizes(self, model_id: str) -> dict[int, int]:
        rows = await self.run(
            "MATCH (:Model {id: $id})-[:HAS_STEP_BLOCK]->(b:StepBlock) RETURN b.first_step AS first, "
            "b.instances AS instances",
            id=model_id,
        )
        return {row["first"]: row["instances"] for row in rows}

    async def block_text(self, model_id: str, first: int) -> str:
        found = await self.run("MATCH (b:StepBlock {id: $id}) RETURN b.text AS text", id=f"{model_id}:{first}")
        return found[0]["text"]

    # Taking a model out

    async def delete_blocks(self, model_id: str) -> None:
        firsts = sorted(await self.block_sizes(model_id))
        for start in range(0, len(firsts), BLOCK_BATCH):
            await self.run(
                "FOR block_id IN $ids MATCH (b:StepBlock {id: block_id}) DETACH DELETE b",
                ids=[f"{model_id}:{first}" for first in firsts[start : start + BLOCK_BATCH]],
            )

    async def delete_entities(self, model_id: str) -> int:
        """In batches of nodes found by their ``id``, so that the work ends whatever a statement does."""
        rows = await self.run("MATCH (n:Entity {model_id: $id}) RETURN n.id AS id", id=model_id)
        ids = [row["id"] for row in rows]
        for start in range(0, len(ids), BATCH):
            await self.run(
                "FOR node_id IN $ids MATCH (n:Entity {id: node_id}) DETACH DELETE n", ids=ids[start : start + BATCH]
            )
        return len(ids)

    async def delete_record(self, model_id: str, *, keep: bool) -> None:
        if keep:
            await self._update(model_id, {}, ("imported_at",))
        else:
            await self.run(
                "MATCH (m:Model {id: $id}) WHERE coalesce(m.format, $step) = $step DETACH DELETE m",
                id=model_id,
                step="step",
            )
