# This file was generated with the assistance of an AI coding tool.
"""What a change in the graph needs, on the graph side and without extras.

:func:`referrers` lists what refers to a node, through relationships and from instances kept in blocks, which the
database does not know of: a node with referrers should not be deleted without them. :func:`refresh_inverses` makes the
inverse relationships of changed nodes anew from their forward ones, since a forward relationship that is changed in the
graph leaves the inverse one as it was. The writer reads neither; both are for the questions asked of the graph.
"""

from __future__ import annotations

import re
from typing import Any

from ifc_lpg.graph import Graph
from ifc_lpg.inverses import inverses
from ifc_lpg.lossless import decode_block
from ifc_lpg.paths import attribute_name
from ifc_lpg.schema import relationship_type
from ifc_lpg.step import statements_of

_REFERENCE = re.compile(rb"#(\d+)")


async def referrers(graph: Graph, model_id: str, step: int) -> list[dict[str, Any]]:
    """What refers to the instance ``step`` of the model, by STEP id: a node as ``{"step", "ifc_class", "attribute",
    "position"}``, and an instance kept in a block as ``{"step", "in_block": True}``."""
    rows = await graph.run(
        "MATCH (a:Entity)-[r]->(n:Entity {id: $id}) WHERE r.position IS NOT NULL "
        "RETURN a.step_id AS step, a.ifc_class AS ifc_class, type(r) AS type, r.position AS position",
        id=f"{model_id}:{step}",
    )
    found: list[dict[str, Any]] = [
        {
            "step": row["step"],
            "ifc_class": row["ifc_class"],
            "attribute": attribute_name(row["type"]),
            "position": row["position"],
        }
        for row in rows
    ]
    for block in await graph.run(
        "MATCH (:Model {id: $model_id})-[:HAS_STEP_BLOCK]->(b:StepBlock) RETURN b.text AS text", model_id=model_id
    ):
        for block_step, statement in statements_of(decode_block(block["text"])):
            if step in (int(number) for number in _REFERENCE.findall(statement.split(b"=", 1)[1])):
                found.append({"step": block_step, "in_block": True})
    return sorted(found, key=lambda referrer: (referrer["step"], str(referrer.get("attribute"))))


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
