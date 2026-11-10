# This file was generated with the assistance of an AI coding tool.
"""Naming an instance by a GlobalId and a path, and finding it again, in the graph and without extras.

A path starts at the nearest instance with a GlobalId and follows IFC's attribute names and list positions, forward
through explicit attributes and backward through inverse ones, which IFC names too:
``Representation.Representations[0].Items[0].StyledByItem[0].Styles[0]`` is a wall's colour. A name on a class is
either forward or inverse, never both, so every path leads to one node or to an error. A list has an order; an inverse
set has none, so its members are ordered by their content, never by STEP id; only members whose content is the same,
and which are alike for that reason, fall back on it. A forward ``[0]`` may be left out: ``Items`` is ``Items[0]``.
A path follows the graph, so in the skeleton form it stops at an instance kept in a block.

:func:`resolve` follows a path to a node of a model. :func:`canonical_path` gives a node the one path it is named by
when several reach it: the shortest, forward steps before inverse ones, the attribute names in alphabetical order, the
lowest position, and the lowest GlobalId. Any path that reaches a node resolves to it; the canonical one is for naming.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from ifc_lpg.graph import Graph
from ifc_lpg.schema import relationship_type

MAX_STEPS = 32  # a path longer than this is refused; the longest canonical path measured was 11 steps
_STEP = re.compile(r"^([A-Za-z][A-Za-z0-9_]*)((?:\[\d+\])*)$")
_INDEX = re.compile(r"\[(\d+)\]")
_LITERAL_FIRST = re.compile(r"^[A-Z_]")
_CONTENT_DEPTH = 3
# IFC attribute names that the relationship type's words do not give back by capitalising each word. Verified against
# IFC2X3, IFC4 and IFC4X3 in the tests; the three names that two attributes share (ID, URLReference, DeltaTConstant) are
# literal attributes, which no path steps through.
_NAMES = {
    relationship_type(name): name
    for name in (
        "ActionID", "AssetID", "CO2Content", "COContent", "DeltaTY", "DeltaTZ", "DeltaT_Y", "DeltaT_Z",
        "MomentOfInertiaYZ", "PHLevel", "PermitID", "ProcedureID", "RequestID", "RotationalDisplacementRX",
        "RotationalDisplacementRY", "RotationalDisplacementRZ", "SourceCRS", "TargetCRS",
    )
}  # fmt: skip


class PathError(ValueError):
    """The path does not lead to one node of the model."""


@dataclass(frozen=True)
class Step:
    """One step of a path: an attribute name and its positions, ``()`` for a single reference."""

    name: str
    positions: tuple[int, ...]

    def __str__(self) -> str:
        return self.name + "".join(f"[{p}]" for p in self.positions)


def parse(path: str) -> tuple[Step, ...]:
    """The steps of ``path``, which is attribute names with positions, joined by dots, or empty for the instance itself."""
    if path == "":
        return ()
    steps = []
    for text in path.split("."):
        match = _STEP.match(text)
        if not match:
            raise PathError(f"{text!r} is not an attribute name with positions")
        steps.append(Step(match.group(1), tuple(int(i) for i in _INDEX.findall(match.group(2)))))
    if len(steps) > MAX_STEPS:
        raise PathError(f"the path has more than {MAX_STEPS} steps")
    return tuple(steps)


def attribute_name(rel_type: str) -> str:
    """The IFC attribute name a relationship type was made from."""
    return _NAMES.get(rel_type) or "".join(part.capitalize() for part in rel_type.split("_"))


def _position_of(value: Any) -> tuple[int, ...]:
    if value is None:
        return ()
    if isinstance(value, list):
        return tuple(int(v) for v in value)
    return (int(value),)


async def _start(graph: Graph, model_id: str, global_id: str) -> dict[str, Any]:
    rows = await graph.run(
        "MATCH (n:Entity {model_id: $model_id, GlobalId: $global_id}) RETURN n.id AS id, n.step_id AS step, "
        "n.ifc_class AS ifc_class",
        model_id=model_id,
        global_id=global_id,
    )
    if not rows:
        raise PathError(f"no instance of the model has the GlobalId {global_id!r}")
    if len(rows) > 1:
        raise PathError(f"{len(rows)} instances of the model have the GlobalId {global_id!r}")
    return rows[0]


async def resolve(graph: Graph, model_id: str, global_id: str, path: str) -> dict[str, Any]:
    """The node that ``path`` from the instance ``global_id`` leads to, as ``{"id", "step", "ifc_class"}``.

    A path that ends in an instance kept in a block gives ``{"step", "in_block": True}``, since that instance is no
    node. Anything else that does not lead to one node is a :class:`PathError`."""
    steps = parse(path)
    node = await _start(graph, model_id, global_id)
    for number, step in enumerate(steps, 1):
        where = f"step {number}, {step}"
        rel_type = relationship_type(step.name)
        rows = await graph.run(
            f"MATCH (n:Entity {{id: $id}})-[r:{rel_type}]->(t:Entity) "
            f"RETURN t.id AS id, t.step_id AS step, t.ifc_class AS ifc_class, r.position AS position",
            id=node["id"],
        )
        wanted = step.positions or (0,)
        if rows and all(row["position"] is None for row in rows):  # inverse: a set, ordered by content
            ordered = await _by_content(graph, [row["id"] for row in rows])
            if len(wanted) != 1 or wanted[0] >= len(ordered):
                raise PathError(f"{where}: {step.name} has {len(ordered)} members, no position {list(wanted)}")
            node = next(row for row in rows if row["id"] == ordered[wanted[0]])
            continue
        found = [row for row in rows if _position_of(row["position"]) == wanted]
        if len(found) == 1:
            node = found[0]
            continue
        # Not a node at that position: perhaps an instance kept in a block, whose STEP id the node keeps.
        in_block = await graph.run(
            f"MATCH (n:Entity {{id: $id}}) RETURN n.`{_safe(step.name)}__step` AS step", id=node["id"]
        )
        kept = in_block[0]["step"] if in_block else None
        if kept is None:
            if rows:
                raise PathError(f"{where}: {step.name} has no position {list(wanted)}")
            raise PathError(f"{where}: {node['ifc_class']} has no reference {step.name}")
        for position in step.positions:
            if not isinstance(kept, list) or position >= len(kept):
                raise PathError(f"{where}: {step.name} has no position {list(step.positions)}")
            kept = kept[position]
        if not isinstance(kept, int):
            raise PathError(f"{where}: {step.name} has no position {list(step.positions)}")
        if number != len(steps):
            raise PathError(f"{where}: {step.name} refers into a block, where no path goes on")
        return {"step": kept, "in_block": True}
    return {"id": node["id"], "step": node["step"], "ifc_class": node["ifc_class"]}


def _safe(name: str) -> str:
    if not _STEP.match(name) or "[" in name:
        raise PathError(f"{name!r} is not an attribute name")
    return name


async def _content(graph: Graph, node_id: str, depth: int, seen: frozenset[str]) -> Any:
    """What a node says, without its STEP id: its literal attributes and, to a depth, what it refers to."""
    rows = await graph.run("MATCH (n:Entity {id: $id}) RETURN properties(n) AS props", id=node_id)
    if not rows:
        return None
    props = {
        key: value
        for key, value in rows[0]["props"].items()
        if _LITERAL_FIRST.match(key) and not key.endswith("__step")
    }
    content: list[Any] = [props]
    if depth > 0 and node_id not in seen:
        refs = await graph.run(
            "MATCH (n:Entity {id: $id})-[r]->(t:Entity) WHERE r.position IS NOT NULL "
            "RETURN type(r) AS type, r.position AS position, t.id AS id",
            id=node_id,
        )
        refs.sort(key=lambda row: (row["type"], _position_of(row["position"])))
        for ref in refs:
            content.append(
                [
                    ref["type"],
                    _position_of(ref["position"]),
                    await _content(graph, ref["id"], depth - 1, seen | {node_id}),
                ]
            )
    return content


async def _by_content(graph: Graph, ids: list[str]) -> list[str]:
    """``ids`` in the order of their content, so that the order owes nothing to STEP ids."""
    keyed = []
    for node_id in ids:
        content = await _content(graph, node_id, _CONTENT_DEPTH, frozenset())
        keyed.append((json.dumps(content, sort_keys=True, default=str), node_id))
    keyed.sort()
    return [node_id for _key, node_id in keyed]


async def canonical_path(graph: Graph, model_id: str, step: int) -> dict[str, Any]:
    """The path the instance ``step`` of the model is named by: ``{"global_id", "path"}``.

    The shortest from an instance with a GlobalId, forward steps before inverse ones, the attribute names in
    alphabetical order, the lowest position and the lowest GlobalId. An instance that no GlobalId reaches, within
    ``MAX_STEPS`` steps, is a :class:`PathError`: only its STEP id names it."""
    target = f"{model_id}:{step}"
    rows = await graph.run("MATCH (n:Entity {id: $id}) RETURN n.GlobalId AS global_id, n.step_id AS step", id=target)
    if not rows:
        raise PathError(f"the model has no node #{step}; an instance kept in a block has none")
    if rows[0]["global_id"] is not None:
        return {"global_id": rows[0]["global_id"], "path": ""}
    # Breadth first, backwards: who refers to the frontier, forward or inversely, until someone has a GlobalId.
    best: dict[str, tuple[tuple[Any, ...], list[Step]]] = {target: ((), [])}
    frontier = [target]
    for _ in range(MAX_STEPS):
        rows = await graph.run(
            "MATCH (p:Entity)-[r]->(t:Entity) WHERE t.id IN $ids AND p.model_id = $model_id "
            "RETURN t.id AS target, p.id AS source, p.GlobalId AS global_id, type(r) AS type, r.position AS position",
            ids=frontier,
            model_id=model_id,
        )
        rooted: list[tuple[tuple[Any, ...], str, str]] = []
        next_frontier: dict[str, tuple[tuple[Any, ...], list[Step]]] = {}
        for row in rows:
            inverse = row["position"] is None
            name = attribute_name(row["type"])
            positions = () if inverse else _position_of(row["position"])
            if inverse:
                members = await graph.run(
                    f"MATCH (p:Entity {{id: $id}})-[:{row['type']}]->(t:Entity) RETURN t.id AS id", id=row["source"]
                )
                ordered = await _by_content(graph, [m["id"] for m in members])
                positions = (ordered.index(row["target"]),)
            tail_key, tail = best[row["target"]]
            step_here = Step(name, positions if (inverse or positions != (0,)) else ())  # [0] is the default forward
            key = (int(inverse), name, positions, *tail_key)
            path = [step_here, *tail]
            if row["global_id"] is not None:
                rooted.append((key, row["global_id"], ".".join(str(s) for s in path)))
            elif row["source"] not in best and (
                row["source"] not in next_frontier or key < next_frontier[row["source"]][0]
            ):
                next_frontier[row["source"]] = (key, path)
        if rooted:
            rooted.sort(key=lambda item: (item[0], item[1]))
            _key, global_id, path = rooted[0]
            return {"global_id": global_id, "path": path}
        if not next_frontier:
            break
        best.update(next_frontier)
        frontier = list(next_frontier)
    raise PathError(f"no GlobalId reaches #{step}; only its STEP id names it")
