# This file was generated with the assistance of an AI coding tool.
"""Load converted records into the graph in batches (``UNWIND … CREATE``, no APOC)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ifc_lpg.graph import FORM_VERSION, Graph, is_whole
from ifc_lpg.lossless import POINTS
from ifc_lpg.schema import relationship_type
from ifc_lpg.schema import safe_identifier as _safe

BATCH = 2_000
MAX_BATCH_BYTES = 16 * 1024 * 1024  # a coordinate list can be megabytes, and a transaction's memory has a limit
BLOCK_BATCH = 100
# A row of a list of points, built in Cypher, since the records are JSON and the package needs no driver of its own.
_POINT = "CASE size(p) WHEN 2 THEN point({x: p[0], y: p[1]}) ELSE point({x: p[0], y: p[1], z: p[2]}) END"
# What an import says of its model, on the Model node. The spatial pass, a preparation and a merge add their own.
MODEL_FACTS = (
    "format",
    "form_version",
    "schema",
    "ifc_project_guid",
    "entities",
    "relationships",
    "skipped",
    "lossless",
    "geometry",
    "ifc_header",
    "imported_at",
)
_OWN = ("spatial_", "prepared_", "merge_")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


async def import_records(graph: Graph, model_id: str, records_path: Path) -> dict[str, Any]:
    """Create the ``Entity`` nodes and their relationships for one model, and its ``Model`` if it is not there.

    The ``Model`` gets what the records say of it (:data:`MODEL_FACTS`), and ``imported_at`` when the last record is
    in. Until then it has none, also when an import starts anew, so that a model with ``imported_at`` is all in the
    graph. Returns those facts, and the counts as ``nodes`` and ``relationships`` too."""
    await graph.run(
        "MERGE (m:Model {id: $model_id}) SET m.format = 'step', m.form_version = $form REMOVE m.imported_at",
        model_id=model_id,
        form=FORM_VERSION,
    )
    header: dict[str, Any] = {}
    nodes = relationships = 0
    block_rows: list[dict[str, Any]] = []
    node_groups: dict[tuple[tuple[str, ...], tuple[str, ...]], list[dict[str, Any]]] = {}
    edge_groups: dict[tuple[str, bool], list[dict[str, Any]]] = {}

    async def flush_nodes() -> None:
        nonlocal nodes
        for (labels, point_keys), rows in node_groups.items():
            label_clause = ":".join(["Entity", *labels])
            points = "".join(
                f", n.{_safe(key)} = [p IN row.points.{_safe(key)} | " + _POINT + "]" for key in point_keys
            )
            # The node is new, so adding the properties gives what replacing them would, and no database then has
            # to take the rest of the clause in a given order: one that replaced last left the node without its id.
            await graph.run(
                f"UNWIND $rows AS row CREATE (n:{label_clause}) SET n += row.props, n.id = row.id, "
                f"n.step_id = row.step, n.model_id = $model_id{points}",
                rows=rows,
                model_id=model_id,
            )
            nodes += len(rows)
        node_groups.clear()
        if block_rows:
            await graph.run(
                """
                MATCH (m:Model {id: $model_id})
                UNWIND $rows AS row
                CREATE (m)-[:HAS_STEP_BLOCK]->(:StepBlock {
                    id: $model_id + ':' + toString(row.first), first_step: row.first, instances: row.instances,
                    text: row.text
                })
                """,
                model_id=model_id,
                rows=block_rows,
            )
            block_rows.clear()

    async def flush_edges() -> None:
        nonlocal relationships
        for (rel_type, positioned), rows in edge_groups.items():
            relationship = f"{rel_type} {{position: row.position}}" if positioned else rel_type
            await graph.run(
                f"UNWIND $rows AS row MATCH (a:Entity {{id: row.a}}), (b:Entity {{id: row.b}}) "
                f"CREATE (a)-[:{relationship}]->(b)",
                rows=rows,
            )
            relationships += len(rows)
        edge_groups.clear()

    pending = pending_bytes = 0
    with records_path.open(encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            kind = record["t"]
            if kind == "n":
                labels = tuple(_safe(label) for label in record["labels"])
                props = record["props"]
                points = {
                    key: value[POINTS] for key, value in props.items() if isinstance(value, dict) and POINTS in value
                }
                if points:
                    props = {key: value for key, value in props.items() if key not in points}
                node_groups.setdefault((labels, tuple(sorted(points))), []).append(
                    {"id": f"{model_id}:{record['step']}", "step": record["step"], "props": props, "points": points}
                )
            elif kind == "g":
                block_rows.append({key: record[key] for key in ("first", "instances", "text")})
            elif kind == "e":
                position = record.get("position")
                row = {"a": f"{model_id}:{record['a']}", "b": f"{model_id}:{record['b']}"}
                if position is not None:
                    row["position"] = position
                edge_groups.setdefault((relationship_type(record["type"]), position is not None), []).append(row)
            else:
                if kind == "h":
                    header = record
                await flush_nodes()  # the header separates nodes from edges
                continue
            pending += 1
            pending_bytes += len(line)
            if pending >= BATCH or pending_bytes >= MAX_BATCH_BYTES:
                await flush_nodes()
                await flush_edges()
                pending = pending_bytes = 0
    await flush_nodes()
    await flush_edges()
    await graph.run(
        """
        MATCH (m:Model {id: $model_id}), (p:Entity:IfcProject {model_id: $model_id})
        MERGE (m)-[:HAS_PROJECT]->(p)
        """,
        model_id=model_id,
    )
    facts = {
        "format": "step",
        "form_version": FORM_VERSION,
        "schema": header.get("schema"),
        "ifc_project_guid": header.get("ifc_project_guid"),
        "entities": nodes,
        "relationships": relationships,
        "skipped": header.get("skipped", 0),
        "lossless": header.get("lossless", False),
        "geometry": header.get("geometry"),
        "ifc_header": header.get("ifc_header"),
        "imported_at": _now(),
    }
    await graph.run("MATCH (m:Model {id: $model_id}) SET m += $facts", model_id=model_id, facts=facts)
    return {"nodes": nodes, "relationships": relationships, **facts}


async def model_facts(graph: Graph, model_id: str) -> dict[str, Any] | None:
    """What this package keeps on a model's ``Model`` node, or None when there is no such model: its ``id``, the facts
    of its import (:data:`MODEL_FACTS`), what the spatial pass, a preparation and a merge have set (``spatial_…``,
    ``prepared_…``, ``merge_…``), and ``whole``, which says that the model is all in the graph. What others keep on
    the node is left out."""
    rows = await graph.run("MATCH (m:Model {id: $id}) RETURN properties(m) AS properties", id=model_id)
    if not rows:
        return None
    properties = rows[0]["properties"]
    own = {key: value for key, value in sorted(properties.items()) if key in MODEL_FACTS or key.startswith(_OWN)}
    return {"id": model_id, "whole": is_whole(properties), **own}


async def delete_blocks(graph: Graph, model_id: str) -> None:
    """Remove a model's blocks, a few at a time, for each holds a lot of text."""
    while True:
        rows = await graph.run(
            """
            MATCH (:Model {id: $model_id})-[:HAS_STEP_BLOCK]->(b:StepBlock)
            WITH b LIMIT $batch DETACH DELETE b RETURN count(*) AS n
            """,
            model_id=model_id,
            batch=BLOCK_BATCH,
        )
        if not rows or int(rows[0]["n"]) < BLOCK_BATCH:
            return


async def delete_model(graph: Graph, model_id: str, *, keep_model: bool = False) -> int:
    """Remove a model's entities and blocks in batches, and the ``Model`` itself unless ``keep_model``. A ``Model``
    that is kept loses its ``imported_at``, since the model is no longer in the graph."""
    deleted = 0
    await delete_blocks(graph, model_id)
    while True:
        rows = await graph.run(
            "MATCH (n:Entity {model_id: $model_id}) WITH n LIMIT $batch DETACH DELETE n RETURN count(*) AS n",
            model_id=model_id,
            batch=BATCH,
        )
        count = int(rows[0]["n"]) if rows else 0
        deleted += count
        if count < BATCH:
            break
    if keep_model:
        await graph.run("MATCH (m:Model {id: $model_id}) REMOVE m.imported_at", model_id=model_id)
    else:
        await graph.run(
            "MATCH (m:Model {id: $model_id}) WHERE coalesce(m.format, 'step') = 'step' DETACH DELETE m",
            model_id=model_id,
        )
    return deleted
