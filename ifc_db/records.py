# This file was generated with the assistance of an AI coding tool.
"""The records a conversion writes, read in batches: what a database is given to hold a model.

A record is a JSON line: ``n`` an instance as a node, ``g`` a block of instances kept as STEP text, ``h`` the header,
which ends the nodes, and ``e`` a reference. The batches keep the nodes of a batch before its references, so that a
reference always finds both ends.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ifc_db.lossless import POINTS
from ifc_db.schema import relationship_type

BATCH = 2_000
MAX_BATCH_BYTES = 16 * 1024 * 1024  # a coordinate list can be megabytes, and a transaction's memory has a limit


@dataclass(frozen=True)
class NewEntity:
    """An instance as a node: the class and its supertypes as labels, its values, and those that are lists of points
    apart, as rows of coordinates."""

    step: int
    labels: tuple[str, ...]
    props: dict[str, Any]
    points: dict[str, list[list[float]]]


@dataclass(frozen=True)
class Block:
    """A run of instances kept as STEP text, deflated and base64-encoded, from the STEP id ``first`` on."""

    first: int
    instances: int
    text: str


@dataclass(frozen=True)
class Reference:
    """A reference from the instance ``a`` to ``b`` as a relationship of the type ``type``. ``position`` is where it
    stands in the attribute, and None for an inverse one, which the file does not say."""

    a: int
    type: str
    b: int
    position: Any = None


@dataclass
class Batch:
    """What is to go in together: the nodes and blocks first, then the references. ``header`` is the header when this
    batch ends at it."""

    entities: list[NewEntity] = field(default_factory=list)
    blocks: list[Block] = field(default_factory=list)
    references: list[Reference] = field(default_factory=list)
    header: dict[str, Any] | None = None


def read_records(path: Path) -> Iterator[Batch]:
    """The records of ``path`` in batches of at most :data:`BATCH` records or :data:`MAX_BATCH_BYTES` bytes. The header
    ends a batch of nodes and blocks; references read before it wait for the next batch."""
    batch = Batch()
    pending = pending_bytes = 0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            kind = record["t"]
            if kind == "n":
                props = record["props"]
                points = {
                    key: value[POINTS] for key, value in props.items() if isinstance(value, dict) and POINTS in value
                }
                if points:
                    props = {key: value for key, value in props.items() if key not in points}
                batch.entities.append(NewEntity(record["step"], tuple(record["labels"]), props, points))
            elif kind == "g":
                batch.blocks.append(Block(record["first"], record["instances"], record["text"]))
            elif kind == "e":
                batch.references.append(
                    Reference(record["a"], relationship_type(record["type"]), record["b"], record.get("position"))
                )
            else:
                yield Batch(batch.entities, batch.blocks, header=record if kind == "h" else None)
                batch = Batch(references=batch.references)
                continue
            pending += 1
            pending_bytes += len(line)
            if pending >= BATCH or pending_bytes >= MAX_BATCH_BYTES:
                yield batch
                batch = Batch()
                pending = pending_bytes = 0
    yield batch
