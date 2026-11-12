# This file was generated with the assistance of an AI coding tool.
"""Neo4j and Memgraph: the same statements in portable Cypher, through any graph with ``run``."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, ClassVar

from ifc_db.backends.base import Backend
from ifc_db.graph import Graph
from ifc_db.records import BATCH, Block, NewEntity, Reference
from ifc_db.schema import safe_identifier as _safe

BLOCK_BATCH = 100
# A row of a list of points, built in Cypher, since the records are JSON and the package needs no driver of its own.
_POINT = "CASE size(p) WHEN 2 THEN point({x: p[0], y: p[1]}) ELSE point({x: p[0], y: p[1], z: p[2]}) END"


class CypherBackend(Backend):
    """A graph that takes Cypher: Neo4j, Memgraph, or a program's own graph with the same ``run``."""

    name: ClassVar[str] = "cypher"

    def __init__(self, graph: Graph) -> None:
        self.graph = graph

    async def model(self, model_id: str) -> dict[str, Any] | None:
        rows = await self.graph.run("MATCH (m:Model {id: $id}) RETURN properties(m) AS properties", id=model_id)
        return rows[0]["properties"] if rows else None

    async def models(self) -> list[dict[str, Any]]:
        return await self.graph.run(
            "MATCH (m:Model) RETURN m.id AS id, m.imported_at AS imported_at, m.state AS state ORDER BY id"
        )

    async def start_import(self, model_id: str, form_version: int) -> None:
        await self.graph.run(
            "MERGE (m:Model {id: $model_id}) SET m.format = 'step', m.form_version = $form REMOVE m.imported_at",
            model_id=model_id,
            form=form_version,
        )

    async def set_facts(self, model_id: str, facts: Mapping[str, Any]) -> None:
        await self.graph.run("MATCH (m:Model {id: $model_id}) SET m += $facts", model_id=model_id, facts=dict(facts))

    async def add_entities(self, model_id: str, entities: Sequence[NewEntity]) -> None:
        """One statement for each set of labels and of properties that are lists of points."""
        groups: dict[tuple[tuple[str, ...], tuple[str, ...]], list[dict[str, Any]]] = {}
        for entity in entities:
            labels = tuple(_safe(label) for label in entity.labels)
            groups.setdefault((labels, tuple(sorted(entity.points))), []).append(
                {"id": f"{model_id}:{entity.step}", "step": entity.step, "props": entity.props, "points": entity.points}
            )
        for (labels, point_keys), rows in groups.items():
            label_clause = ":".join(["Entity", *labels])
            points = "".join(
                f", n.{_safe(key)} = [p IN row.points.{_safe(key)} | " + _POINT + "]" for key in point_keys
            )
            # The node is new, so adding the properties gives what replacing them would, and no database then has to
            # take the rest of the clause in a given order: one that replaced last left the node without its id.
            await self.graph.run(
                f"UNWIND $rows AS row CREATE (n:{label_clause}) SET n += row.props, n.id = row.id, "
                f"n.step_id = row.step, n.model_id = $model_id{points}",
                rows=rows,
                model_id=model_id,
            )

    async def add_blocks(self, model_id: str, blocks: Sequence[Block]) -> None:
        if blocks:
            await self.graph.run(
                """
                MATCH (m:Model {id: $model_id})
                UNWIND $rows AS row
                CREATE (m)-[:HAS_STEP_BLOCK]->(:StepBlock {
                    id: $model_id + ':' + toString(row.first), first_step: row.first, instances: row.instances,
                    text: row.text
                })
                """,
                model_id=model_id,
                rows=[{"first": block.first, "instances": block.instances, "text": block.text} for block in blocks],
            )

    async def add_references(self, model_id: str, references: Sequence[Reference]) -> None:
        """One statement for each type of relationship, with its position or, for an inverse one, without."""
        groups: dict[tuple[str, bool], list[dict[str, Any]]] = {}
        for reference in references:
            row: dict[str, Any] = {"a": f"{model_id}:{reference.a}", "b": f"{model_id}:{reference.b}"}
            if reference.position is not None:
                row["position"] = reference.position
            groups.setdefault((reference.type, reference.position is not None), []).append(row)
        for (rel_type, positioned), rows in groups.items():
            relationship = f"{_safe(rel_type)} {{position: row.position}}" if positioned else _safe(rel_type)
            await self.graph.run(
                f"UNWIND $rows AS row MATCH (a:Entity {{id: row.a}}), (b:Entity {{id: row.b}}) "
                f"CREATE (a)-[:{relationship}]->(b)",
                rows=rows,
            )

    async def link_project(self, model_id: str) -> None:
        await self.graph.run(
            """
            MATCH (m:Model {id: $model_id}), (p:Entity:IfcProject {model_id: $model_id})
            MERGE (m)-[:HAS_PROJECT]->(p)
            """,
            model_id=model_id,
        )

    async def steps(self, model_id: str) -> list[int]:
        rows = await self.graph.run("MATCH (n:Entity {model_id: $id}) RETURN n.step_id AS step", id=model_id)
        return [row["step"] for row in rows]

    async def entity_page(self, model_id: str, steps: Sequence[int]) -> list[tuple[int, dict[str, Any], list[Any]]]:
        rows = await self.graph.run(
            """
            UNWIND $ids AS node_id
            MATCH (n:Entity {id: node_id})
            OPTIONAL MATCH (n)-[r]->(t:Entity)
            WHERE r.position IS NOT NULL
            WITH n, CASE WHEN r IS NULL THEN NULL ELSE [type(r), r.position, t.step_id] END AS reference
            RETURN n.step_id AS step, properties(n) AS props, collect(reference) AS references
            """,
            ids=[f"{model_id}:{step}" for step in steps],
        )
        return [(row["step"], row["props"], row["references"]) for row in rows]

    async def block_sizes(self, model_id: str) -> dict[int, int]:
        rows = await self.graph.run(
            """
            MATCH (:Model {id: $id})-[:HAS_STEP_BLOCK]->(b:StepBlock)
            RETURN b.id AS id, b.first_step AS first, b.instances AS instances
            """,
            id=model_id,
        )
        return {row["first"]: row["instances"] for row in rows}

    async def block_text(self, model_id: str, first: int) -> str:
        found = await self.graph.run("MATCH (b:StepBlock {id: $id}) RETURN b.text AS text", id=f"{model_id}:{first}")
        return found[0]["text"]

    async def delete_blocks(self, model_id: str) -> None:
        """A few at a time, for each holds a lot of text."""
        while True:
            rows = await self.graph.run(
                """
                MATCH (:Model {id: $model_id})-[:HAS_STEP_BLOCK]->(b:StepBlock)
                WITH b LIMIT $batch DETACH DELETE b RETURN count(*) AS n
                """,
                model_id=model_id,
                batch=BLOCK_BATCH,
            )
            if not rows or int(rows[0]["n"]) < BLOCK_BATCH:
                return

    async def delete_entities(self, model_id: str) -> int:
        deleted = 0
        while True:
            rows = await self.graph.run(
                "MATCH (n:Entity {model_id: $model_id}) WITH n LIMIT $batch DETACH DELETE n RETURN count(*) AS n",
                model_id=model_id,
                batch=BATCH,
            )
            count = int(rows[0]["n"]) if rows else 0
            deleted += count
            if count < BATCH:
                return deleted

    async def delete_record(self, model_id: str, *, keep: bool) -> None:
        if keep:
            await self.graph.run("MATCH (m:Model {id: $model_id}) REMOVE m.imported_at", model_id=model_id)
        else:
            await self.graph.run(
                "MATCH (m:Model {id: $model_id}) WHERE coalesce(m.format, 'step') = 'step' DETACH DELETE m",
                model_id=model_id,
            )
