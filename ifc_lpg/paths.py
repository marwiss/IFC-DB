# This file was generated with the assistance of an AI coding tool.
"""Naming an instance by a GlobalId and a path, and finding it again, in the graph and without extras.

A path starts at the nearest instance with a GlobalId and follows IFC's attribute names and list positions, forward
through explicit attributes and backward through inverse ones, which IFC names too:
``Representation.Representations[0].Items[0].StyledByItem[0].Styles[0]`` is a wall's colour. A name on a class is
either forward or inverse, never both, so every path leads to one node or to an error. A list has an order; an inverse
set has none, so its members are ordered by their content, never by STEP id; only members whose content is the same,
and which are alike for that reason, fall back on it. A forward ``[0]`` may be left out: ``Items`` is ``Items[0]``.
A path follows the graph. In the skeleton form it can reach an instance kept in a block and go on only backwards from
it, through the few inverse attributes a node can stand in for such an instance (``_FROM_BLOCK``), as from a mesh to
its ``StyledByItem``; the referring node keeps the instance's STEP id in ``<attribute>__step``.

:func:`unreachable` lists the nodes that no path reaches, which only their STEP id names.
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
from ifc_lpg.lossless import STEP_SUFFIX, decode_block
from ifc_lpg.schema import relationship_type
from ifc_lpg.step import statements_of

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


# An inverse step that starts at an instance kept in a block: the class and the attribute by which a node refers to it.
# These are the inverse attributes of the classes kept in blocks whose referring class can be a node; verified against
# IFC2X3, IFC4 and IFC4X3 in the tests.
_FROM_BLOCK = {
    "StyledByItem": ("IfcStyledItem", "Item"),
    "LayerAssignment": ("IfcPresentationLayerAssignment", "AssignedItems"),
    "LayerAssignments": ("IfcPresentationLayerAssignment", "AssignedItems"),
    "HasColours": ("IfcIndexedColourMap", "MappedTo"),
    "HasTextures": ("IfcIndexedTextureMap", "MappedTo"),
    "ToFaceSet": ("IfcPolygonalFaceSet", "Faces"),
    "HasTextureMaps": ("IfcTextureMap", "MappedTo"),
    "HasTexCoords": ("IfcTextureCoordinateIndices", "TexCoordsOf"),
    "AnnotatedSurface": ("IfcAnnotationSurface", "TextureCoordinates"),
}


def _inverse_name(label: str, attribute: str, schema: str) -> str | None:
    """The inverse a node of ``label`` stands in through ``attribute`` for the instance in a block it refers to."""
    names = [name for name, pair in _FROM_BLOCK.items() if pair == (label, attribute)]
    if len(names) > 1:  # the layers: plural in IFC2X3, singular since
        return "LayerAssignments" if schema.upper().startswith("IFC2X3") else "LayerAssignment"
    return names[0] if names else None


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
    current: dict[str, Any] | int = await _start(graph, model_id, global_id)
    for number, step in enumerate(steps, 1):
        where = f"step {number}, {step}"
        if isinstance(current, int):
            current = await _from_block(graph, model_id, current, step, where)
            continue
        node = current
        rows = await graph.run(
            f"MATCH (n:Entity {{id: $id}})-[r:{relationship_type(step.name)}]->(t:Entity) "
            f"RETURN t.id AS id, t.step_id AS step, t.ifc_class AS ifc_class, r.position AS position",
            id=node["id"],
        )
        wanted = step.positions or (0,)
        if rows and all(row["position"] is None for row in rows):  # inverse: a set, ordered by content
            current = await _member(graph, rows, wanted, step, where)
            continue
        found = [row for row in rows if _position_of(row["position"]) == wanted]
        if len(found) == 1:
            current = found[0]
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
        positions = step.positions or ((0,) if isinstance(kept, list) else ())  # a forward [0] is the default
        for position in positions:
            if not isinstance(kept, list) or position >= len(kept):
                raise PathError(f"{where}: {step.name} has no position {list(positions)}")
            kept = kept[position]
        if not isinstance(kept, int):
            raise PathError(f"{where}: {step.name} has no position {list(positions)}")
        current = kept
    if isinstance(current, int):
        return {"step": current, "in_block": True}
    return {"id": current["id"], "step": current["step"], "ifc_class": current["ifc_class"]}


async def _member(
    graph: Graph, rows: list[dict[str, Any]], wanted: tuple[int, ...], step: Step, where: str
) -> dict[str, Any]:
    ordered = await _by_content(graph, [row["id"] for row in rows])
    if len(wanted) != 1 or wanted[0] >= len(ordered):
        raise PathError(f"{where}: {step.name} has {len(ordered)} members, no position {list(wanted)}")
    return next(row for row in rows if row["id"] == ordered[wanted[0]])


async def _referring_from_block(graph: Graph, model_id: str, block_step: int, name: str) -> list[dict[str, Any]]:
    """The nodes that stand in the inverse ``name`` of the instance ``block_step``, kept in a block."""
    label, attribute = _FROM_BLOCK[name]
    rows = await graph.run(
        f"MATCH (n:{_safe(label)} {{model_id: $model_id}}) WHERE n.`{_safe(attribute)}__step` IS NOT NULL "
        f"RETURN n.id AS id, n.step_id AS step, n.ifc_class AS ifc_class, n.GlobalId AS global_id, "
        f"n.`{attribute}__step` AS refs",
        model_id=model_id,
    )
    return [
        row
        for row in rows
        if row["refs"] == block_step or (isinstance(row["refs"], list) and block_step in row["refs"])
    ]


async def _from_block(graph: Graph, model_id: str, block_step: int, step: Step, where: str) -> dict[str, Any]:
    if step.name not in _FROM_BLOCK:
        raise PathError(f"{where}: #{block_step} is kept in a block, and a path goes on from it only backwards")
    members = await _referring_from_block(graph, model_id, block_step, step.name)
    if not members:
        raise PathError(f"{where}: #{block_step}, kept in a block, has no {step.name}")
    return await _member(graph, members, step.positions or (0,), step, where)


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
    alphabetical order, the lowest position and the lowest GlobalId. The search goes through instances kept in blocks as
    :func:`resolve` does. An instance that no GlobalId reaches, within ``MAX_STEPS`` steps, is a :class:`PathError`:
    only its STEP id names it."""
    target_id = f"{model_id}:{step}"
    rows = await graph.run("MATCH (n:Entity {id: $id}) RETURN n.GlobalId AS global_id", id=target_id)
    if rows and rows[0]["global_id"] is not None:
        return {"global_id": rows[0]["global_id"], "path": ""}
    model = await graph.run("MATCH (m:Model {id: $id}) RETURN m.schema AS schema", id=model_id)
    schema = (model[0]["schema"] if model else None) or ""
    start: str | int = target_id if rows else step  # a node, or an instance kept in a block
    # Breadth first, backwards: who refers to the frontier, until someone has a GlobalId.
    best: dict[str | int, tuple[tuple[Any, ...], list[Step]]] = {start: ((), [])}
    frontier: list[str | int] = [start]
    for _ in range(MAX_STEPS):
        rooted: list[tuple[tuple[Any, ...], str, str]] = []
        next_frontier: dict[str | int, tuple[tuple[Any, ...], list[Step]]] = {}
        for source, global_id, inverse, name, positions, item in await _referrers(graph, model_id, schema, frontier):
            tail_key, tail = best[item]
            key = (int(inverse), name, positions, *tail_key)
            written = positions if (inverse or positions != (0,)) else ()  # a forward [0] is the default
            path = [Step(name, written), *tail]
            if global_id is not None:
                rooted.append((key, global_id, ".".join(str(s) for s in path)))
            elif source not in best and (source not in next_frontier or key < next_frontier[source][0]):
                next_frontier[source] = (key, path)
        if rooted:
            rooted.sort(key=lambda found: (found[0], found[1]))
            _key, global_id, path = rooted[0]
            return {"global_id": global_id, "path": path}
        if not next_frontier:
            break
        best.update(next_frontier)
        frontier = list(next_frontier)
    if not rows:
        raise PathError(f"the model has no node #{step}, and no GlobalId reaches it through a block")
    raise PathError(f"no GlobalId reaches #{step}; only its STEP id names it")


Referrer = tuple[str | int, str | None, bool, str, tuple[int, ...], str | int]


async def _referrers(graph: Graph, model_id: str, schema: str, frontier: list[str | int]) -> list[Referrer]:
    """Who refers to the frontier, as ``(source, its GlobalId, inverse, attribute name, positions, item)``: the step
    from the source to the item. A source or an item is a node's id, or the STEP id of an instance kept in a block."""
    found: list[Referrer] = []
    nodes = [item for item in frontier if isinstance(item, str)]
    blocks = {item for item in frontier if isinstance(item, int)}
    if nodes:
        rows = await graph.run(
            "MATCH (p:Entity)-[r]->(t:Entity) WHERE t.id IN $ids AND p.model_id = $model_id "
            "RETURN t.id AS target, p.id AS source, p.GlobalId AS global_id, type(r) AS type, r.position AS position",
            ids=nodes,
            model_id=model_id,
        )
        for row in rows:
            name = attribute_name(row["type"])
            if row["position"] is None:  # inverse: the position among the source's members, by content
                members = await graph.run(
                    f"MATCH (p:Entity {{id: $id}})-[:{row['type']}]->(t:Entity) RETURN t.id AS id", id=row["source"]
                )
                ordered = await _by_content(graph, [m["id"] for m in members])
                position = (ordered.index(row["target"]),)
                found.append((row["source"], row["global_id"], True, name, position, row["target"]))
            else:
                position = _position_of(row["position"])
                found.append((row["source"], row["global_id"], False, name, position, row["target"]))
        # A node that stands in an inverse of an instance kept in a block: that instance comes before it on the path.
        labels = " OR ".join(f"n:{_safe(label)}" for label in sorted({label for label, _ in _FROM_BLOCK.values()}))
        standing = await graph.run(
            f"MATCH (n:Entity) WHERE n.id IN $ids AND ({labels}) "
            "RETURN n.id AS id, labels(n) AS labels, properties(n) AS props",
            ids=nodes,
        )
        for row in standing:
            for label, attribute in sorted(set(_FROM_BLOCK.values())):
                name = _inverse_name(label, attribute, schema)
                refs = row["props"].get(attribute + "__step")
                if label not in row["labels"] or name is None or refs is None:
                    continue
                for block_step in refs if isinstance(refs, list) else [refs]:
                    members = await _referring_from_block(graph, model_id, block_step, name)
                    ordered = await _by_content(graph, [m["id"] for m in members])
                    found.append((block_step, None, True, name, (ordered.index(row["id"]),), row["id"]))
    if blocks:
        rows = await graph.run(
            "MATCH (n:Entity {model_id: $model_id}) WHERE any(k IN keys(n) WHERE k ENDS WITH '__step') "
            "RETURN n.id AS id, n.GlobalId AS global_id, properties(n) AS props",
            model_id=model_id,
        )
        for row in rows:
            for key, value in row["props"].items():
                if not key.endswith("__step"):
                    continue
                name = key[: -len("__step")]
                listed = list(enumerate(value)) if isinstance(value, list) else [(None, value)]
                for position, block_step in listed:
                    if block_step in blocks:
                        positions = () if position is None else (position,)
                        found.append((row["id"], row["global_id"], False, name, positions, block_step))
    return found


_REFERENCE = re.compile(rb"#(\d+)")


def _steps_of(value: Any) -> list[int]:
    return (
        [v for v in value if isinstance(v, int)]
        if isinstance(value, list)
        else [value]
        if isinstance(value, int)
        else []
    )


async def unreachable(graph: Graph, model_id: str) -> list[dict[str, Any]]:
    """The nodes of the model that no GlobalId reaches, forward or backward, as ``{"step", "ifc_class"}`` by STEP id.

    They are what only a STEP id names, such as unused placements; a check can warn of them, and a change can remove
    them. The search follows the relationships both ways, the inverse attributes a node stands in for an instance kept
    in a block (``_FROM_BLOCK``), and counts a node that a block refers to as reached, since the database does not know
    that reference and the node is not to be removed. Instances kept in blocks are not listed: only their STEP id names
    them, and they cannot be changed through the graph."""
    nodes = await graph.run(
        "MATCH (n:Entity {model_id: $model_id}) RETURN n.id AS id, n.step_id AS step, n.ifc_class AS ifc_class, "
        "n.GlobalId IS NOT NULL AS rooted",
        model_id=model_id,
    )
    edges = await graph.run(
        "MATCH (a:Entity {model_id: $model_id})-[]->(b:Entity) RETURN a.id AS a, b.id AS b", model_id=model_id
    )
    out: dict[str, set[str]] = {}
    for edge in edges:
        out.setdefault(edge["a"], set()).add(edge["b"])
    # What a node refers to in a block, by attribute; and the nodes that stand in an inverse of an instance in a block.
    refers = await graph.run(
        "MATCH (n:Entity {model_id: $model_id}) WHERE any(k IN keys(n) WHERE k ENDS WITH '__step') "
        "RETURN n.id AS id, labels(n) AS labels, properties(n) AS props",
        model_id=model_id,
    )
    into_blocks: dict[str, set[int]] = {}
    standing: dict[int, set[str]] = {}
    for row in refers:
        for key, value in row["props"].items():
            if key.endswith(STEP_SUFFIX):
                into_blocks.setdefault(row["id"], set()).update(_steps_of(value))
        for label, attribute in set(_FROM_BLOCK.values()):
            if label in row["labels"]:
                for block_step in _steps_of(row["props"].get(attribute + STEP_SUFFIX)):
                    standing.setdefault(block_step, set()).add(row["id"])
    from_blocks: set[int] = set()
    for block in await graph.run(
        "MATCH (:Model {id: $model_id})-[:HAS_STEP_BLOCK]->(b:StepBlock) RETURN b.text AS text", model_id=model_id
    ):
        for _step, statement in statements_of(decode_block(block["text"])):
            from_blocks.update(int(number) for number in _REFERENCE.findall(statement.split(b"=", 1)[1]))
    reached = {node["id"] for node in nodes if node["rooted"] or node["step"] in from_blocks}
    ids = {node["id"] for node in nodes}
    frontier = list(reached)
    while frontier:
        found: set[str] = set()
        for node_id in frontier:
            found |= out.get(node_id, set())
            for block_step in into_blocks.get(node_id, ()):
                found |= standing.get(block_step, set())
                if f"{model_id}:{block_step}" in ids:  # a node held by its STEP id, as an owner history is
                    found.add(f"{model_id}:{block_step}")
        frontier = list(found - reached)
        reached |= found
    left = [{"step": node["step"], "ifc_class": node["ifc_class"]} for node in nodes if node["id"] not in reached]
    return sorted(left, key=lambda node: node["step"])
