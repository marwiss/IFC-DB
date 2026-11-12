# This file was generated with the assistance of an AI coding tool.
"""Read a model out of the graph, for ``writer.py`` to write back as a file."""

from __future__ import annotations

import heapq
import json
import tempfile
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, TextIO

from ifc_db.graph import Graph, is_whole
from ifc_db.lossless import NATIVE, decode_block
from ifc_db.step import statements_of

PAGE = 1_000
_ID_WIDTH = 20  # a STEP id, zero-padded, so that lines sort by it as text


class NotExportable(ValueError):
    """This model cannot be written back. The message says why."""


def node_line(step: int, properties: dict[str, Any], references: list[Any]) -> str:
    """One node as a line that sorts by its id, with the id in front to be cut off in the end.

    A node can hold what JSON has no form for, such as a date or bytes that a program put there. Such a value goes through
    marked: the writer passes over it, unless it stands where an attribute does. A point is a tuple, and goes as a list."""
    record = json.dumps({"step": step, "props": properties, "references": references}, default=_marked)
    return f"{step:0{_ID_WIDTH}d} {record}\n"


def _marked(value: Any) -> dict[str, str]:
    return {NATIVE: type(value).__name__}


def block_lines(text: str) -> Iterator[str]:
    """The instances of one block as such lines. Bytes above 0x7F can stand in them, so they go through as Latin-1."""
    for step, statement in statements_of(decode_block(text)):
        record = json.dumps({"step": step, "line": statement.decode("latin-1")})
        yield f"{step:0{_ID_WIDTH}d} {record}\n"


def write_merged(out: TextIO, *sorted_lines: Iterable[str]) -> None:
    """Runs of such lines as one in order of id, without holding them in memory."""
    for line in heapq.merge(*sorted_lines):
        out.write(line[_ID_WIDTH + 1 :])


async def export_records(graph: Graph, model_id: str, out: Path) -> int:
    """Write the records of a model to ``out``, in the form ``writer.py`` reads. Returns the number of instances.

    Relationships with a ``position`` are what the file says. The inverse ones and ``SAME_AS`` have none, so they stay
    behind, and so does any node that was not converted from the file, since only nodes of the model are asked for.
    The nodes are fetched by their ids in pages, each page by its index, so that the work does not grow with the
    square of the model's size as paging by ``ORDER BY`` would.
    """
    rows = await graph.run(
        """
        MATCH (m:Model {id: $id})
        RETURN m.state AS state, m.imported_at AS imported_at, m.lossless AS lossless, m.schema AS schema,
               m.ifc_header AS ifc_header, m.skipped AS in_blocks, m.format AS format
        """,
        id=model_id,
    )
    if not rows:
        raise NotExportable("there is no such model")
    model = rows[0]
    if (model["format"] or "step") != "step":
        raise NotExportable(f"the model is {model['format']}, not STEP")
    if not is_whole(model):
        state = f": it is {model['state']}" if model["state"] else ""
        raise NotExportable(f"the model's import is not done{state}")
    if not model["lossless"]:
        raise NotExportable("the model was converted without the lossless form, so the graph does not hold it all")
    blocks = await graph.run(
        """
        MATCH (:Model {id: $id})-[:HAS_STEP_BLOCK]->(b:StepBlock)
        RETURN b.id AS id, b.first_step AS first, b.instances AS instances
        """,
        id=model_id,
    )
    held = sum(block["instances"] for block in blocks)
    if held != (model["in_blocks"] or 0):
        raise NotExportable(f"the graph holds {held} of the {model['in_blocks']} instances that were kept in blocks")
    steps = await graph.run("MATCH (n:Entity {model_id: $id}) RETURN n.step_id AS step", id=model_id)
    ordered = sorted(row["step"] for row in steps)
    with tempfile.TemporaryDirectory(prefix="ifcgraph-") as tmp:
        nodes_path, blocks_path = Path(tmp) / "nodes", Path(tmp) / "blocks"
        with nodes_path.open("w", encoding="utf-8") as handle:
            for start in range(0, len(ordered), PAGE):
                page = await graph.run(
                    """
                    UNWIND $ids AS node_id
                    MATCH (n:Entity {id: node_id})
                    OPTIONAL MATCH (n)-[r]->(t:Entity)
                    WHERE r.position IS NOT NULL
                    WITH n, CASE WHEN r IS NULL THEN NULL ELSE [type(r), r.position, t.step_id] END AS reference
                    RETURN n.step_id AS step, properties(n) AS props, collect(reference) AS references
                    """,
                    ids=[f"{model_id}:{step}" for step in ordered[start : start + PAGE]],
                )
                for row in sorted(page, key=lambda row: row["step"]):
                    handle.write(node_line(row["step"], row["props"], row["references"]))
        with blocks_path.open("w", encoding="utf-8") as handle:
            for block in sorted(blocks, key=lambda row: row["first"]):
                found = await graph.run("MATCH (b:StepBlock {id: $id}) RETURN b.text AS text", id=block["id"])
                handle.writelines(block_lines(found[0]["text"]))
        with out.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps({"schema": model["schema"], "ifc_header": model["ifc_header"]}) + "\n")
            with nodes_path.open(encoding="utf-8") as nodes, blocks_path.open(encoding="utf-8") as blocks:
                write_merged(handle, nodes, blocks)
    return len(ordered) + held
