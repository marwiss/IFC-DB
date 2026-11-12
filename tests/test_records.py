# This file was generated with the assistance of an AI coding tool.
"""The records read in batches: nodes and blocks before references, the header ending the nodes."""

from __future__ import annotations

import json
from pathlib import Path

from ifc_db import records
from ifc_db.records import Block, NewEntity, Reference, read_records


def _write(path: Path, *lines: dict) -> Path:
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    return path


def test_the_header_ends_the_nodes_and_a_reference_waits_for_the_next_batch(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "r.jsonl",
        {"t": "n", "step": 1, "labels": ["IfcWall"], "props": {"Name": "A", "CoordList": {"$points": [[0.0, 1.0]]}}},
        {"t": "g", "first": 2, "instances": 3, "text": "eJw="},
        {"t": "e", "a": 1, "type": "ObjectPlacement", "b": 7, "position": 0},
        {"t": "h", "schema": "IFC4"},
        {"t": "e", "a": 7, "type": "PlacesObject", "b": 1},
    )
    first, second = read_records(path)
    assert first.entities == [NewEntity(1, ("IfcWall",), {"Name": "A"}, {"CoordList": [[0.0, 1.0]]})]
    assert (
        first.blocks == [Block(2, 3, "eJw=")]
        and first.references == []
        and first.header == {"t": "h", "schema": "IFC4"}
    )
    assert second.entities == [] and second.header is None
    assert second.references == [Reference(1, "OBJECT_PLACEMENT", 7, 0), Reference(7, "PLACES_OBJECT", 1, None)]


def test_a_batch_ends_at_its_size(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(records, "BATCH", 2)
    lines = [{"t": "n", "step": step, "labels": ["IfcWall"], "props": {}} for step in range(1, 6)]
    batches = list(read_records(_write(tmp_path / "r.jsonl", *lines)))
    assert [[entity.step for entity in batch.entities] for batch in batches] == [[1, 2], [3, 4], [5]]
