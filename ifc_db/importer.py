# This file was generated with the assistance of an AI coding tool.
"""Load converted records into a database in batches, through its backend."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ifc_db.backends import Backend, as_backend
from ifc_db.graph import FORM_VERSION, Graph, is_whole
from ifc_db.records import read_records

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


async def import_records(graph: Graph | Backend, model_id: str, records_path: Path) -> dict[str, Any]:
    """Create the ``Entity`` nodes and their relationships for one model, and its ``Model`` if it is not there.

    The ``Model`` gets what the records say of it (:data:`MODEL_FACTS`), and ``imported_at`` when the last record is
    in. Until then it has none, also when an import starts anew, so that a model with ``imported_at`` is all in the
    graph. Returns those facts, and the counts as ``nodes`` and ``relationships`` too."""
    backend = as_backend(graph)
    await backend.start_import(model_id, FORM_VERSION)
    header: dict[str, Any] = {}
    nodes = relationships = 0
    for batch in read_records(records_path):
        await backend.add_entities(model_id, batch.entities)
        await backend.add_blocks(model_id, batch.blocks)
        await backend.add_references(model_id, batch.references)
        nodes += len(batch.entities)
        relationships += len(batch.references)
        if batch.header is not None:
            header = batch.header
    await backend.link_project(model_id)
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
    await backend.set_facts(model_id, facts)
    return {"nodes": nodes, "relationships": relationships, **facts}


async def model_facts(graph: Graph | Backend, model_id: str) -> dict[str, Any] | None:
    """What this package keeps on a model's ``Model`` node, or None when there is no such model: its ``id``, the facts
    of its import (:data:`MODEL_FACTS`), what the spatial pass, a preparation and a merge have set (``spatial_…``,
    ``prepared_…``, ``merge_…``), and ``whole``, which says that the model is all in the graph. What others keep on
    the node is left out."""
    properties = await as_backend(graph).model(model_id)
    if properties is None:
        return None
    own = {key: value for key, value in sorted(properties.items()) if key in MODEL_FACTS or key.startswith(_OWN)}
    return {"id": model_id, "whole": is_whole(properties), **own}


async def models(graph: Graph | Backend) -> list[dict[str, Any]]:
    """The models in the graph, in the order of their ids: ``{"id", "whole"}`` for each, where ``whole`` says that the
    model is all in the graph."""
    return [{"id": row["id"], "whole": is_whole(row)} for row in await as_backend(graph).models()]


async def delete_blocks(graph: Graph | Backend, model_id: str) -> None:
    """Remove a model's blocks, a few at a time, for each holds a lot of text."""
    await as_backend(graph).delete_blocks(model_id)


async def delete_model(graph: Graph | Backend, model_id: str, *, keep_model: bool = False) -> int:
    """Remove a model's entities and blocks in batches, and the ``Model`` itself unless ``keep_model``. A ``Model``
    that is kept loses its ``imported_at``, since the model is no longer in the graph."""
    backend = as_backend(graph)
    await backend.delete_blocks(model_id)
    deleted = await backend.delete_entities(model_id)
    await backend.delete_record(model_id, keep=keep_model)
    return deleted
