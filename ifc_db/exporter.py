# This file was generated with the assistance of an AI coding tool.
"""Read a model out of the graph, for ``writer.py`` to write back as a file."""

from __future__ import annotations

import heapq
import json
import tempfile
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, TextIO

from ifc_db.backends import Backend, as_backend
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


async def export_records(graph: Graph | Backend, model_id: str, out: Path) -> int:
    """Write the records of a model to ``out``, in the form ``writer.py`` reads. Returns the number of instances.

    Relationships with a ``position`` are what the file says. The inverse ones and ``SAME_AS`` have none, so they stay
    behind, and so does any node that was not converted from the file, since only nodes of the model are asked for.
    The nodes are fetched by their ids in pages, each page by its index, so that the work does not grow with the
    square of the model's size as paging by ``ORDER BY`` would.
    """
    backend = as_backend(graph)
    model = await backend.model(model_id)
    if model is None:
        raise NotExportable("there is no such model")
    if (model.get("format") or "step") != "step":
        raise NotExportable(f"the model is {model['format']}, not STEP")
    if not is_whole(model):
        state = f": it is {model['state']}" if model.get("state") else ""
        raise NotExportable(f"the model's import is not done{state}")
    if not model.get("lossless"):
        raise NotExportable("the model was converted without the lossless form, so the graph does not hold it all")
    sizes = await backend.block_sizes(model_id)
    held, in_blocks = sum(sizes.values()), model.get("skipped") or 0
    if held != in_blocks:
        raise NotExportable(f"the graph holds {held} of the {in_blocks} instances that were kept in blocks")
    ordered = sorted(await backend.steps(model_id))
    with tempfile.TemporaryDirectory(prefix="ifcgraph-") as tmp:
        nodes_path, blocks_path = Path(tmp) / "nodes", Path(tmp) / "blocks"
        with nodes_path.open("w", encoding="utf-8") as handle:
            for start in range(0, len(ordered), PAGE):
                page = await backend.entity_page(model_id, ordered[start : start + PAGE])
                for step, props, references in sorted(page, key=lambda row: row[0]):
                    handle.write(node_line(step, props, references))
        with blocks_path.open("w", encoding="utf-8") as handle:
            for first in sorted(sizes):
                handle.writelines(block_lines(await backend.block_text(model_id, first)))
        with out.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps({"schema": model.get("schema"), "ifc_header": model.get("ifc_header")}) + "\n")
            with nodes_path.open(encoding="utf-8") as nodes, blocks_path.open(encoding="utf-8") as blocks:
                write_merged(handle, nodes, blocks)
    return len(ordered) + held
