# This file was generated with the assistance of an AI coding tool.
"""What a change in the graph needs, on the graph side and without extras.

:func:`referrers` lists what refers to a node, through relationships and from instances kept in blocks, which the
database does not know of: a node with referrers should not be deleted without them, and :func:`delete_nodes` deletes
nodes only when nothing else refers to them. :func:`refresh_inverses` makes the inverse relationships of changed nodes
anew from their forward ones, since a forward relationship that is changed in the graph leaves the inverse one as it was.
"""

from __future__ import annotations

import re
from typing import Any

from ifc_db.graph import Graph
from ifc_db.importer import BATCH
from ifc_db.inverses import inverses
from ifc_db.lossless import decode_block
from ifc_db.paths import attribute_name
from ifc_db.schema import relationship_type
from ifc_db.step import statements_of

_REFERENCE = re.compile(rb"#(\d+)")


class StillReferred(ValueError):
    """Something outside the nodes to delete refers to them, so nothing was deleted. ``referrers`` says what."""

    def __init__(self, referrers: list[dict[str, Any]]) -> None:
        listed = ", ".join(f"#{r['step']} to #{r['target']}" for r in referrers[:10])
        super().__init__(f"{len(referrers)} references to what was to be deleted: {listed}")
        self.referrers = referrers


async def _referrers(graph: Graph, model_id: str, steps: list[int]) -> list[dict[str, Any]]:
    """What refers to the instances ``steps``, with the one each refers to as ``target``."""
    wanted = set(steps)
    found: list[dict[str, Any]] = []
    for start in range(0, len(steps), BATCH):
        for row in await graph.run(
            "UNWIND $ids AS id MATCH (a:Entity)-[r]->(n:Entity {id: id}) WHERE r.position IS NOT NULL "
            "RETURN a.step_id AS step, a.ifc_class AS ifc_class, type(r) AS type, r.position AS position, "
            "n.step_id AS target",
            ids=[f"{model_id}:{step}" for step in steps[start : start + BATCH]],
        ):
            rel_type = row.pop("type")
            found.append({**row, "attribute": attribute_name(rel_type)})
    for block in await graph.run(
        "MATCH (:Model {id: $model_id})-[:HAS_STEP_BLOCK]->(b:StepBlock) RETURN b.text AS text", model_id=model_id
    ):
        for block_step, statement in statements_of(decode_block(block["text"])):
            refers = {int(number) for number in _REFERENCE.findall(statement.split(b"=", 1)[1])}
            found.extend({"step": block_step, "in_block": True, "target": t} for t in sorted(refers & wanted))
    return sorted(found, key=lambda r: (r["target"], r["step"], str(r.get("attribute")), str(r.get("position"))))


async def referrers(graph: Graph, model_id: str, step: int) -> list[dict[str, Any]]:
    """What refers to the instance ``step`` of the model, by STEP id: a node as ``{"step", "ifc_class", "attribute",
    "position"}``, and an instance kept in a block as ``{"step", "in_block": True}``."""
    found = await _referrers(graph, model_id, [step])
    return [{key: value for key, value in r.items() if key != "target"} for r in found]


async def delete_nodes(graph: Graph, model_id: str, steps: list[int]) -> int:
    """Delete the nodes ``steps`` of the model, with their relationships, when nothing else refers to them: no other
    node, and no instance kept in a block, whose reference the database does not know and would point to nothing in the
    written file. Otherwise nothing is deleted, and :class:`StillReferred` lists what refers. An instance kept in a
    block cannot be deleted here. Returns how many nodes went."""
    wanted = sorted(set(steps))
    found = set()
    for start in range(0, len(wanted), BATCH):
        rows = await graph.run(
            "UNWIND $ids AS id MATCH (n:Entity {id: id}) RETURN n.step_id AS step",
            ids=[f"{model_id}:{step}" for step in wanted[start : start + BATCH]],
        )
        found.update(row["step"] for row in rows)
    if missing := [step for step in wanted if step not in found]:
        raise ValueError(f"the model has no node {', '.join(f'#{s}' for s in missing[:10])}; one in a block cannot go")
    outside = [r for r in await _referrers(graph, model_id, wanted) if r["step"] not in found or r.get("in_block")]
    if outside:
        raise StillReferred(outside)
    for start in range(0, len(wanted), BATCH):
        await graph.run(
            "UNWIND $ids AS id MATCH (n:Entity {id: id}) DETACH DELETE n",
            ids=[f"{model_id}:{step}" for step in wanted[start : start + BATCH]],
        )
    return len(wanted)


async def refresh_inverses(graph: Graph, model_id: str, steps: list[int]) -> dict[str, int]:
    """Make the inverse relationships of the nodes ``steps`` anew from their forward relationships, as the import does.

    Call it with the nodes whose forward relationships were changed. Returns how many inverse relationships were removed
    and created."""
    model = await graph.run("MATCH (m:Model {id: $id}) RETURN m.schema AS schema", id=model_id)
    if not model or not model[0]["schema"]:
        raise ValueError(f"the model {model_id!r} has no schema")
    by_reference: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for declaring, name, referring, attribute in inverses(model[0]["schema"]):
        by_reference.setdefault((referring, attribute), []).append((declaring, name))
    ids = [f"{model_id}:{step}" for step in steps]
    removed = await graph.run(
        "UNWIND $ids AS node_id MATCH (:Entity)-[r]->(a:Entity {id: node_id}) WHERE r.position IS NULL "
        "DELETE r RETURN count(*) AS n",
        ids=ids,
    )
    rows = await graph.run(
        "UNWIND $ids AS node_id MATCH (a:Entity {id: node_id})-[r]->(t:Entity) WHERE r.position IS NOT NULL "
        "RETURN a.id AS a, labels(a) AS a_labels, type(r) AS type, t.id AS t, labels(t) AS t_labels",
        ids=ids,
    )
    wanted: dict[str, set[tuple[str, str]]] = {}
    for row in rows:
        attribute = attribute_name(row["type"])
        for label in row["a_labels"]:
            for declaring, name in by_reference.get((label, attribute), ()):
                if declaring in row["t_labels"]:
                    wanted.setdefault(relationship_type(name), set()).add((row["t"], row["a"]))
    created = 0
    for rel_type, pairs in wanted.items():
        await graph.run(
            f"UNWIND $rows AS row MATCH (t:Entity {{id: row.t}}), (a:Entity {{id: row.a}}) CREATE (t)-[:{rel_type}]->(a)",
            rows=[{"t": t, "a": a} for t, a in sorted(pairs)],
        )
        created += len(pairs)
    return {"removed": int(removed[0]["n"]) if removed else 0, "created": created}
