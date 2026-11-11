# This file was generated with the assistance of an AI coding tool.
"""Bring a building of one model into another model's project, as a third model, in the graph.

The building of the model B comes into the project of the model A as the model C; A and B do not change. With the
building comes what lies below it, through aggregation, nesting, containment, openings, fillings, coverings and
projections; the relationships that concern it, with what they define, such as types and property sets; the styles and
layers of its shapes; and everything those refer to. A relationship that also concerns something outside the building
keeps only what is inside, and one that points outside alone, such as a group, a system or a connection to another
building, is left out. The building stands below A's site as it stood below its own, and its shapes use A's
representation contexts where A has the same ones.

Identity is by GlobalId: an object of B with a GlobalId that A has is the same object. Where the two have the same
content, C has A's. Where they differ, a choice is made for it: ``"a"`` keeps A's, and B's relationships keep it only
where they also concern something else of B; ``"b"`` puts B's in its place, and A's relationships that concern it alone
are taken out; ``"new"`` makes two objects, B's with a new GlobalId. A STEP id decides nothing: C keeps A's, and the
instances from B are numbered after them.

C is ``running`` until it is checked: it is written out, and IfcOpenShell's validator must find nothing in it that A and B
do not have. Otherwise C is taken out again and the merge fails with the issues. The schema and the units of A and B must
be the same, and so must the form of their geometry; :func:`prepare_other` makes B ready first where its schema or its
length unit differ.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import sys
import tempfile
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ifc_lpg.convert import BLOCK_BYTES
from ifc_lpg.editing import refresh_inverses
from ifc_lpg.exporter import export_records
from ifc_lpg.files import DEFAULT_LIMITS, Limits, _now, export_file, import_file, run_worker, write_file
from ifc_lpg.graph import FORM_VERSION, Graph
from ifc_lpg.importer import BATCH, BLOCK_BATCH, delete_model
from ifc_lpg.lossless import ALL_ARGUMENTS, STEP_SUFFIX, TAGGED, TYPE_SUFFIX, decode_block, encode_block
from ifc_lpg.schema import relationship_type, safe_identifier
from ifc_lpg.step import parse_statement, statement_text, statements_of, string_of_atom

CHOICES = ("a", "b", "new")
_GUID = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_$"
_DERIVED = "REMOVE c.bbox_min, c.bbox_max, c.bbox_centre"  # the boxes are worked out again for C
# Downward from the building: a relationship of a class, its attributes on the upper side, and the one below.
_STRUCTURE = {
    "IfcRelAggregates": (("RelatingObject",), "RelatedObjects"),
    "IfcRelNests": (("RelatingObject",), "RelatedObjects"),
    "IfcRelContainedInSpatialStructure": (("RelatingStructure",), "RelatedElements"),
    "IfcRelVoidsElement": (("RelatingBuildingElement",), "RelatedOpeningElement"),
    "IfcRelFillsElement": (("RelatingOpeningElement",), "RelatedBuildingElement"),
    "IfcRelCoversBldgElements": (("RelatingBuildingElement",), "RelatedCoverings"),
    "IfcRelCoversSpaces": (("RelatingSpace", "RelatedSpace"), "RelatedCoverings"),
    "IfcRelProjectsElement": (("RelatingElement",), "RelatedFeatureElement"),
    "IfcRelAdheresToElement": (("RelatingElement",), "RelatedSurfaceFeatures"),
}
_DEFINITIONS = ("IfcTypeObject", "IfcPropertyDefinition")  # what comes along with what it defines
# What nothing refers to but which belongs to the shapes it refers to: styles, layers and the maps of a mesh, with the
# attribute that ties each to them, and that attribute's place among the parameters of an instance kept in a block.
_ATTACHED = {
    "IfcStyledItem": "Item",
    "IfcPresentationLayerAssignment": "AssignedItems",
    "IfcIndexedColourMap": "MappedTo",
    "IfcTextureMap": "MappedTo",
    "IfcIndexedTextureMap": "MappedTo",
}
_ATTACHED_KEYWORDS = {
    b"IFCSTYLEDITEM": 0,
    b"IFCPRESENTATIONLAYERASSIGNMENT": 2,
    b"IFCPRESENTATIONLAYERWITHSTYLE": 2,
    b"IFCINDEXEDCOLOURMAP": 0,
    b"IFCTEXTUREMAP": 2,
    b"IFCINDEXEDTRIANGLETEXTUREMAP": 1,
    b"IFCINDEXEDPOLYGONALTEXTUREMAP": 1,
}
# The attributes of relationships that are lists, by the class that declares them, with the schemas they are in (2 for
# IFC2X3, 4 for IFC4, 3 for IFC4X3). A test checks the table against IfcOpenShell.
_LISTS = """
IfcRelAdheresToElement RelatedSurfaceFeatures 3
IfcRelAggregates RelatedObjects 43
IfcRelAssigns RelatedObjects 243
IfcRelAssociates RelatedObjects 243
IfcRelConnectsPathElements RelatedPriorities 243
IfcRelConnectsPathElements RelatingPriorities 243
IfcRelConnectsWithRealizingElements RealizingElements 243
IfcRelContainedInSpatialStructure RelatedElements 243
IfcRelCoversBldgElements RelatedCoverings 243
IfcRelCoversSpaces RelatedCoverings 243
IfcRelDeclares RelatedDefinitions 43
IfcRelDecomposes RelatedObjects 2
IfcRelDefines RelatedObjects 2
IfcRelDefinesByObject RelatedObjects 43
IfcRelDefinesByProperties RelatedObjects 43
IfcRelDefinesByTemplate RelatedPropertySets 43
IfcRelDefinesByType RelatedObjects 43
IfcRelFlowControlElements RelatedControlElements 243
IfcRelNests RelatedObjects 43
IfcRelOverridesProperties OverridingProperties 2
IfcRelPositions RelatedProducts 3
IfcRelReferencedInSpatialStructure RelatedElements 243
IfcRelServicesBuildings RelatedBuildings 243
"""


def list_attributes(schema: str) -> list[tuple[str, str]]:
    """``(declaring class, attribute)`` for every attribute of a relationship that is a list in ``schema``."""
    family = "2" if schema.upper().startswith("IFC2X3") else "3" if schema.upper().startswith("IFC4X3") else "4"
    return [
        (declaring, attribute)
        for declaring, attribute, schemas in (line.split(" ") for line in _LISTS.strip().splitlines())
        if family in schemas
    ]


_CONTEXT_KEYWORDS = {b"IFCGEOMETRICREPRESENTATIONCONTEXT": False, b"IFCGEOMETRICREPRESENTATIONSUBCONTEXT": True}
_OWNER = relationship_type("OwnerHistory")
_PLACEMENT = relationship_type("ObjectPlacement")


class MergeError(ValueError):
    """The building cannot be brought into the project. The message says why."""


class MergeConflict(MergeError):
    """Objects of both models with one GlobalId differ, and no choice was made for them. ``report`` lists them."""

    def __init__(self, report: dict[str, Any]) -> None:
        open_ = [c["global_id"] for c in report["collisions"] if c["choice"] is None]
        super().__init__(f"{len(open_)} objects differ in the two models and need a choice: {', '.join(open_[:10])}")
        self.report = report


def new_global_id() -> str:
    """A new GlobalId: 128 random bits in IFC's 22 characters."""
    number, characters = uuid.uuid4().int, []
    for _ in range(22):
        number, rest = divmod(number, 64)
        characters.append(_GUID[rest])
    return "".join(reversed(characters))


def _ids(value: Any) -> list[int]:
    """The references in a STEP value."""
    if isinstance(value, int):
        return [value]
    if isinstance(value, list):
        return [found for item in value for found in _ids(item)]
    if isinstance(value, tuple):
        return _ids(value[1])
    return []


def _tagged_ids(value: Any) -> list[int]:
    """The references to what is no node in tagged JSON."""
    if isinstance(value, list):
        return [found for item in value for found in _tagged_ids(item)]
    if isinstance(value, dict):
        if "d" in value:
            return [value["d"]]
        if "v" in value:
            return _tagged_ids(value["v"])
    return []


def _map_step(value: Any, to: Callable[[int], Any]) -> Any:
    """A STEP value with its references through ``to``."""
    if isinstance(value, int):
        return to(value)
    if isinstance(value, list):
        return [_map_step(item, to) for item in value]
    if isinstance(value, tuple):
        return (value[0], [_map_step(item, to) for item in value[1]])
    return value


def _map_parameters(parameters: list[Any], to: Callable[[int], Any], dropped: set[int]) -> list[Any]:
    """An instance's parameters through ``to``, with the ``dropped`` references taken out of the lists among them."""
    return [
        _map_step([item for item in value if not (isinstance(item, int) and item in dropped)], to)
        if isinstance(value, list)
        else _map_step(value, to)
        for value in parameters
    ]


def _map_tagged(value: Any, to: Callable[[int], Any], dropped: set[int], positions: dict[int, int | None]) -> Any:
    """Tagged JSON with its references renumbered: ``{"d": id}`` through ``to``, and ``{"r": k}`` to the new position
    of its relationship; an entry of a list whose reference is taken out goes too."""

    def gone(item: Any) -> bool:
        return isinstance(item, dict) and (item.get("d") in dropped or positions.get(item.get("r", -1), 0) is None)

    if isinstance(value, list):
        return [_map_tagged(item, to, dropped, positions) for item in value if not gone(item)]
    if isinstance(value, dict):
        if "d" in value:
            return {"d": to(value["d"])}
        if "r" in value:
            return {"r": positions.get(value["r"], value["r"])}
        if "v" in value:
            return {**value, "v": _map_tagged(value["v"], to, set(), positions)}
    return value


def _parse(statement: bytes) -> tuple[bytes, list[Any]]:
    return parse_statement(statement[statement.index(b"=") + 1 : statement.rindex(b";")])


def _text(atom: Any) -> Any:
    """A value of a block's instance as a node would hold it, for comparing the two."""
    if not isinstance(atom, bytes) or atom in (b"$", b"*"):
        return None
    if atom[:1] == b"s":
        return string_of_atom(atom)
    if atom[:1] == b"." and atom[-1:] == b".":
        return atom[1:-1].decode("ascii")
    return atom.decode("latin-1")


@dataclass
class _Instance:
    """An instance of a model as the merge needs it: what it is, and every reference it makes."""

    step: int
    labels: frozenset[str] = frozenset()
    global_id: str | None = None
    edges: list[tuple[str, Any, int]] = field(default_factory=list)  # forward: (type, position, target)
    steps: dict[str, Any] = field(default_factory=dict)  # <attribute>__step
    tagged: dict[str, str] = field(default_factory=dict)  # tagged JSON, and ifc_args
    keyword: bytes = b""
    parameters: list[Any] | None = None  # for an instance kept in a block

    @property
    def in_block(self) -> bool:
        return self.parameters is not None

    def is_a(self, label: str) -> bool:
        return label in self.labels

    def targets(self) -> set[int]:
        found = {target for _type, _position, target in self.edges}
        for value in self.steps.values():
            found.update(value if isinstance(value, list) else [value])
        for text in self.tagged.values():
            found.update(_tagged_ids(json.loads(text)))
        if self.parameters is not None:
            found.update(_ids(self.parameters))
        return found

    def attribute(self, name: str) -> list[int]:
        rel_type = relationship_type(name)
        return [target for kind, _position, target in self.edges if kind == rel_type]


@dataclass
class _Model:
    id: str
    fields: dict[str, Any]
    instances: dict[int, _Instance]
    referrers: dict[int, list[int]]
    props: dict[int, dict[str, Any]] = field(default_factory=dict)

    def single(self, label: str) -> int:
        found = [step for step, instance in self.instances.items() if instance.is_a(label)]
        if len(found) != 1:
            raise MergeError(f"the model {self.id!r} has {len(found)} instances of {label}, not one")
        return found[0]


async def _load(graph: Graph, model_id: str) -> _Model:
    rows = await graph.run(
        "MATCH (m:Model {id: $id}) RETURN m.format AS format, m.state AS state, m.lossless AS lossless, "
        "m.schema AS schema, m.geometry AS geometry, m.skipped AS skipped, m.ifc_header AS ifc_header, "
        "m.ifc_project_guid AS ifc_project_guid",
        id=model_id,
    )
    if not rows:
        raise MergeError(f"there is no model {model_id!r}")
    fields = rows[0]
    if (fields["format"] or "step") != "step" or fields["state"] != "done" or not fields["lossless"]:
        raise MergeError(f"the model {model_id!r} is not a STEP model that is done, in the lossless form")
    instances: dict[int, _Instance] = {}
    for row in await graph.run(
        "MATCH (n:Entity {model_id: $id}) RETURN n.step_id AS step, labels(n) AS labels, n.GlobalId AS global_id",
        id=model_id,
    ):
        instances[row["step"]] = _Instance(row["step"], frozenset(row["labels"]), row["global_id"])
    for row in await graph.run(
        "MATCH (n:Entity {model_id: $id}) WHERE any(k IN keys(n) WHERE k ENDS WITH '__step' OR k ENDS WITH '__type' "
        "OR k = 'ifc_args') RETURN n.step_id AS step, properties(n) AS props",
        id=model_id,
    ):
        instance, props = instances[row["step"]], row["props"]
        for key, value in props.items():
            if key.endswith(STEP_SUFFIX):
                instance.steps[key] = value
            elif key == ALL_ARGUMENTS or props.get(key + TYPE_SUFFIX) == TAGGED:
                instance.tagged[key] = value
    for row in await graph.run(
        "MATCH (n:Entity {model_id: $id})-[r]->(t:Entity {model_id: $id}) WHERE r.position IS NOT NULL "
        "RETURN n.step_id AS a, type(r) AS type, r.position AS position, t.step_id AS b",
        id=model_id,
    ):
        instances[row["a"]].edges.append((row["type"], row["position"], row["b"]))
    for block in await graph.run(
        "MATCH (:Model {id: $id})-[:HAS_STEP_BLOCK]->(b:StepBlock) RETURN b.text AS text", id=model_id
    ):
        for step, statement in statements_of(decode_block(block["text"])):
            keyword, parameters = _parse(statement)
            instances[step] = _Instance(step, keyword=keyword, parameters=parameters)
    referrers: dict[int, list[int]] = {}
    for instance in instances.values():
        instance.edges.sort(key=lambda edge: (edge[0], json.dumps(edge[1])))
        for target in instance.targets():
            referrers.setdefault(target, []).append(instance.step)
    model = _Model(model_id, fields, instances, referrers)
    shared = [
        s for s, i in instances.items() if i.is_a("IfcGeometricRepresentationContext") or i.is_a("IfcApplication")
    ]
    await _fetch(graph, model, shared)
    return model


async def _fetch(graph: Graph, model: _Model, steps: Iterable[int]) -> None:
    """Read the properties of the nodes ``steps`` that are not read yet."""
    wanted = sorted(
        {s for s in steps if s not in model.props and s in model.instances and not model.instances[s].in_block}
    )
    for start in range(0, len(wanted), BATCH):
        for row in await graph.run(
            "UNWIND $ids AS id MATCH (n:Entity {id: id}) RETURN n.step_id AS step, properties(n) AS props",
            ids=[f"{model.id}:{s}" for s in wanted[start : start + BATCH]],
        ):
            model.props[row["step"]] = row["props"]


def _closure(model: _Model, starts: Iterable[int], stop: Iterable[int] = ()) -> set[int]:
    """What ``starts`` refer to, on to the next rooted instance or one in ``stop``."""
    stopped, found = set(stop), set(starts)
    frontier = list(found)
    while frontier:
        step = frontier.pop()
        for target in model.instances[step].targets():
            instance = model.instances.get(target)
            if instance is None or target in found:
                continue
            found.add(target)
            if instance.global_id is None and target not in stopped:
                frontier.append(target)
    return found


def _content(model: _Model, step: int, memo: dict[int, str], fixed: dict[int, str]) -> str:
    """What an instance holds, with what it refers to: an instance with a GlobalId by that, one in ``fixed`` by what
    is given there, and every other by its content. The owner history does not count."""
    if step in memo:
        return memo[step]
    memo[step] = "cycle"

    def ref(target: int) -> str:
        if target in fixed:
            return fixed[target]
        instance = model.instances.get(target)
        if instance is None:
            return "#"
        if instance.global_id is not None:
            return "G" + instance.global_id
        return _content(model, target, memo, fixed)

    instance = model.instances[step]
    if instance.parameters is not None:
        body = repr((instance.keyword, _map_step(instance.parameters, ref)))
    else:
        items = []
        for key, value in sorted(model.props[step].items()):
            if key.startswith("OwnerHistory") or not (key[:1].isupper() or key in ("ifc_class", ALL_ARGUMENTS)):
                continue
            if key.endswith(STEP_SUFFIX):
                value = [ref(target) for target in (value if isinstance(value, list) else [value])]
            elif key in instance.tagged:
                value = json.dumps(_map_tagged(json.loads(value), ref, set(), {}))
            items.append((key, value))
        edges = sorted(
            (kind, repr(position), ref(target)) for kind, position, target in instance.edges if kind != _OWNER
        )
        body = repr((items, edges))
    memo[step] = hashlib.sha256(body.encode("utf-8", "surrogatepass")).hexdigest()
    return memo[step]


def _context_key(model: _Model, step: int | None) -> tuple[Any, ...] | None:
    """What makes two representation contexts the same: the kind, the identifier, the type, the view and the parent."""
    instance = model.instances.get(step) if step is not None else None
    if instance is None:
        return None
    if instance.parameters is not None:
        sub = _CONTEXT_KEYWORDS.get(instance.keyword)
        if sub is None:
            return None
        values = instance.parameters
        identifier, kind = _text(values[0]), _text(values[1])
        view = _text(values[8]) if sub and len(values) > 8 else None
        parent = values[6] if sub and len(values) > 6 and isinstance(values[6], int) else None
    else:
        if not instance.is_a("IfcGeometricRepresentationContext"):
            return None
        sub = instance.is_a("IfcGeometricRepresentationSubContext")
        props = model.props.get(step, {})
        identifier, kind = props.get("ContextIdentifier"), props.get("ContextType")
        view = props.get("TargetView") if sub else None
        parents = instance.attribute("ParentContext") or [instance.steps.get("ParentContext" + STEP_SUFFIX)]
        parent = parents[0] if sub else None
    return (sub, identifier, kind, view, _context_key(model, parent) if sub else None)


def _related_of(instance: _Instance) -> str | None:
    """The relationship type of the lower side of a relationship of the structure, which places what it holds."""
    for label, (_relating, related) in _STRUCTURE.items():
        if instance.is_a(label):
            return relationship_type(related)
    return None


def _anchors(instance: _Instance) -> list[int] | None:
    """What an attached instance belongs to, such as the item of a styled item; None for one that is not attached."""
    if instance.global_id is not None:
        return None
    if instance.parameters is not None:
        index = _ATTACHED_KEYWORDS.get(instance.keyword)
        return None if index is None or index >= len(instance.parameters) else _ids(instance.parameters[index])
    for label, name in _ATTACHED.items():
        if instance.is_a(label):
            value = instance.steps.get(name + STEP_SUFFIX)
            found = instance.attribute(name) + (value if isinstance(value, list) else [] if value is None else [value])
            return found + (_tagged_ids(json.loads(instance.tagged[name])) if name in instance.tagged else [])
    return None


def _application_keys(model: _Model, step: int) -> list[tuple[Any, ...]]:
    """What the schema keeps unique among applications: the identifier, and the full name with the version."""
    instance = model.instances.get(step)
    if instance is None or not instance.is_a("IfcApplication"):
        return []
    props = model.props.get(step, {})
    return [
        ("IfcApplication", props.get("ApplicationIdentifier")),
        ("IfcApplication", props.get("ApplicationFullName"), props.get("Version")),
    ]


def _has_owner(instance: _Instance) -> bool:
    return bool(instance.attribute("OwnerHistory")) or "OwnerHistory" + STEP_SUFFIX in instance.steps


def _owner_of_base(a: _Model) -> int | None:
    """The owner history of A's project, when A's schema needs one on every rooted instance (IFC2X3): what of B has none
    gets it, as a model migrated from IFC4 has none. Only the project links to its owner history; every other instance
    holds its STEP id in ``OwnerHistory__step``, as the import does."""
    if not str(a.fields["schema"]).upper().startswith("IFC2X3"):
        return None
    project = a.instances[a.single("IfcProject")]
    found = project.attribute("OwnerHistory")
    return found[0] if found else None


def _placement_of(instance: _Instance) -> int | None:
    found = instance.attribute("ObjectPlacement") or [instance.steps.get("ObjectPlacement" + STEP_SUFFIX)]
    return found[0]


@dataclass
class _Selection:
    """What of B goes into C, and how."""

    objects: set[int]  # what lies below the building, and the building
    chosen: set[int] = field(default_factory=set)  # B's instances that C gets
    subst: dict[int, int] = field(default_factory=dict)  # B's instances that A's stand in for in C
    drop: dict[int, set[int]] = field(default_factory=dict)  # what is taken out of a chosen instance's lists
    left_out: list[int] = field(default_factory=list)  # relationships that point outside alone
    upward: int | None = None  # what put the building below its site in B
    new_contexts: list[int] = field(default_factory=list)  # root contexts that A does not have
    placements: int = 0  # placements outside the building that A's site's stands in for


def _select(
    b: _Model,
    building: int,
    site_placement: int,
    contexts: dict[Any, int],
    mapped: dict[int, int],
    placements: dict[int, int] | None = None,
    extra: tuple[int, ...] | list[int] = (),
) -> _Selection:
    """What of B comes into C with the building, when the instances ``mapped`` are A's in C, and the ``placements`` of
    those that are products A's placements of them."""
    instances = b.instances
    objects, frontier = {building}, [building]
    while frontier:
        upper = frontier.pop()
        for step in b.referrers.get(upper, ()):
            relation = instances[step]
            for label, (relating, related) in _STRUCTURE.items():
                if relation.is_a(label) and any(upper in relation.attribute(name) for name in relating):
                    for lower in relation.attribute(related):
                        if lower not in objects:
                            objects.add(lower)
                            frontier.append(lower)
    outside = {
        _placement_of(instance)
        for step, instance in instances.items()
        if instance.is_a("IfcProduct") and step not in objects
    } - {None}
    selection = _Selection(objects, subst={**(placements or {}), **mapped})
    queue: list[int] = []
    decided: set[int] = set()
    attached: set[int] = set()

    def take(step: int) -> None:
        if step not in selection.chosen and step not in selection.subst:
            selection.chosen.add(step)
            queue.append(step)

    def kind(target: int) -> str:
        if target in selection.subst:
            return "mapped"
        if target in selection.chosen or target in objects:
            return "inside"
        instance = instances.get(target)
        if instance is not None and instance.global_id is not None and not any(map(instance.is_a, _DEFINITIONS)):
            return "foreign"
        return "other"

    def decide(step: int) -> None:
        decided.add(step)
        relation = instances[step]
        groups: dict[str, list[tuple[int, str]]] = {}
        for rel_type, _position, target in relation.edges:
            groups.setdefault(rel_type, []).append((target, kind(target)))
        kinds = [{k for _t, k in members} for members in groups.values()]
        if any("foreign" in ks and not ks & {"inside", "mapped"} for ks in kinds):
            if (
                relation.is_a("IfcRelAggregates")
                and building in relation.attribute("RelatedObjects")
                and kind(building) == "inside"
            ):
                selection.upward = step
            else:
                selection.left_out.append(step)
            return
        related = _related_of(relation)
        if related in groups and all(k == "mapped" for _t, k in groups[related]):
            return  # what A has keeps its place in A's structure
        if not any("inside" in ks for ks in kinds):
            return  # about A's versions alone, which keep A's relationships
        dropped = {
            target
            for rel_type, members in groups.items()
            for target, k in members
            if k == "foreign"
            or (k == "mapped" and (rel_type == related or any(other == "inside" for _t, other in members)))
        }
        if dropped:
            selection.drop[step] = dropped
        take(step)

    for step in [*objects, *extra]:
        take(step)
    while queue:
        while queue:
            step = queue.pop()
            instance = instances[step]
            for target in sorted(instance.targets() - selection.drop.get(step, set())):
                if target in selection.subst or target not in instances:
                    continue
                if target in outside:
                    selection.subst[target] = site_placement
                    selection.placements += 1
                    continue
                key = _context_key(b, target)
                if key is not None and key in contexts:
                    selection.subst[target] = contexts[key]
                    continue
                same = [contexts[k] for k in _application_keys(b, target) if k in contexts]
                if same:  # the schema has one application of an identifier, and of a name and version
                    selection.subst[target] = same[0]
                    continue
                if key is not None and not key[0] and target not in selection.chosen:
                    selection.new_contexts.append(target)
                take(target)
            for referrer in b.referrers.get(step, ()):
                other = instances[referrer]
                if referrer in decided or referrer in selection.chosen:
                    continue
                if instance.global_id is not None and other.is_a("IfcRelationship"):
                    decide(referrer)
                elif _anchors(other) is not None:
                    attached.add(referrer)
        # Styles and layers once all else is known, so that a list of them keeps all that comes along.
        for step in sorted(attached - selection.chosen):
            decided.add(step)
            instance = instances[step]
            groups: dict[Any, list[int]] = {}
            for rel_type, _position, target in instance.edges:
                groups.setdefault(rel_type, []).append(target)
            for key, value in instance.steps.items():
                groups.setdefault(relationship_type(key[: -len(STEP_SUFFIX)]), []).extend(
                    value if isinstance(value, list) else [value]
                )
            for key, text in instance.tagged.items():
                groups.setdefault(relationship_type(key), []).extend(_tagged_ids(json.loads(text)))
            for index, value in enumerate(instance.parameters or ()):
                groups[index] = _ids(value)
            inside = set(selection.chosen) | set(selection.subst)
            dropped = {t for members in groups.values() if set(members) & inside for t in members if t not in inside}
            if dropped:
                selection.drop[step] = dropped
            take(step)
        attached.clear()
    return selection


@dataclass
class _Plan:
    a: _Model
    b: _Model
    building: int
    site: int
    site_placement: int
    contexts: dict[Any, int]
    collisions: list[tuple[int, int, str | None]]  # B's instance, A's, and the choice
    report: dict[str, Any]
    units: list[int] = field(default_factory=list)  # B's units of a kind that A has none of


def _assignment(model: _Model) -> int | None:
    found = model.instances[model.single("IfcProject")].attribute("UnitsInContext")
    return found[0] if found else None


def _unit_kind(props: dict[str, Any]) -> tuple[Any, ...]:
    """What a unit is a unit of: its type, the user's name of it where it is user defined, or money."""
    if props.get("ifc_class") == "IfcMonetaryUnit":
        return ("money",)
    return (props.get("UnitType"), props.get("UserDefinedType") if props.get("UnitType") == "USERDEFINED" else None)


def _unit_name(props: dict[str, Any]) -> str:
    kind = props.get("UnitType") or "money"
    return f"{kind} {props.get('Prefix') or ''}{props.get('Name') or props.get('Currency') or ''}".strip()


_PREFIX = {
    "EXA": 1e18, "PETA": 1e15, "TERA": 1e12, "GIGA": 1e9, "MEGA": 1e6, "KILO": 1e3, "HECTO": 1e2, "DECA": 1e1,
    "DECI": 1e-1, "CENTI": 1e-2, "MILLI": 1e-3, "MICRO": 1e-6, "NANO": 1e-9, "PICO": 1e-12, "FEMTO": 1e-15, "ATTO": 1e-18,
}  # fmt: skip


def _scale(model: _Model, step: int, depth: int = 0) -> tuple[Any, float] | None:
    """A unit's SI unit and how many of that it is, through the factors of conversion-based units."""
    props = model.props.get(step, {})
    if depth > 8:
        return None
    if props.get("ifc_class") == "IfcSIUnit":
        return props.get("Name"), _PREFIX.get(str(props.get("Prefix")), 1.0)
    measure = model.instances[step].attribute("ConversionFactor") if step in model.instances else []
    if not measure:
        return None
    value = model.props.get(measure[0], {}).get("ValueComponent")
    inner = model.instances[measure[0]].attribute("UnitComponent")
    found = _scale(model, inner[0], depth + 1) if inner else None
    if not isinstance(value, int | float) or isinstance(value, bool) or found is None:
        return None
    return found[0], value * found[1]


def _meaning(model: _Model, step: int) -> tuple[Any, ...]:
    """What a unit means, whatever it is called: its kind, its SI unit and its scale to nine digits, so that 'DEGREE'
    and 'degree' of the same factor are one unit; money by its currency, and a derived unit by its parts."""
    props = model.props.get(step, {})
    kind = _unit_kind(props)
    if props.get("ifc_class") == "IfcMonetaryUnit":
        return (*kind, props.get("Currency"))
    if props.get("ifc_class") == "IfcDerivedUnit":
        parts = []
        for element in model.instances[step].attribute("Elements"):
            unit = model.instances[element].attribute("Unit")
            parts.append((_meaning(model, unit[0]) if unit else None, model.props.get(element, {}).get("Exponent")))
        return (*kind, tuple(sorted(parts, key=repr)))
    found = _scale(model, step)
    if found is None:
        return (*kind, props.get("ifc_class"), str(props.get("Name")).upper())
    return (*kind, found[0], float(f"{found[1]:.9g}"))


async def _unit_meanings(graph: Graph, model: _Model) -> list[tuple[int, tuple[Any, ...]]]:
    assignment = _assignment(model)
    units = model.instances[assignment].attribute("Units") if assignment is not None else []
    await _fetch(graph, model, _closure(model, units))
    return [(unit, _meaning(model, unit)) for unit in units]


async def _units(graph: Graph, a: _Model, b: _Model) -> list[int]:
    """B's units against A's (F29 a): each the same as one of A's, by what it means, or of a kind that A has no unit of,
    which C takes from B. A unit of a kind A has, but another, is refused. Returns B's units that C takes."""
    known = {meaning for _unit, meaning in await _unit_meanings(graph, a)}
    kinds = {meaning[:2] for meaning in known}
    other = [(unit, meaning) for unit, meaning in await _unit_meanings(graph, b) if meaning not in known]
    differing = [unit for unit, meaning in other if meaning[:2] in kinds]
    if differing:
        names = "; ".join(_unit_name(b.props[unit]) for unit in differing)
        raise MergeError(f"the units differ: A does not have {names} of B; prepare_other converts B's lengths")
    if other and _assignment(a) is None:
        raise MergeError("A has no unit assignment to take B's units into")
    return [unit for unit, _meaning in other]


def _check_choice(choice: str | None) -> None:
    if choice is not None and choice not in CHOICES:
        raise ValueError(f"a choice is one of {', '.join(CHOICES)}, not {choice!r}")


async def _plan(
    graph: Graph,
    base_id: str,
    other_id: str,
    building: str,
    site: str | None,
    choices: dict[str, str],
    default: str | None,
) -> _Plan:
    _check_choice(default)
    for choice in choices.values():
        _check_choice(choice)
    a, b = await _load(graph, base_id), await _load(graph, other_id)
    for name in ("schema", "geometry"):
        if a.fields[name] != b.fields[name]:
            raise MergeError(
                f"the {name} of A is {a.fields[name]} and of B {b.fields[name]}; they must be the same"
                + ("; prepare_other migrates B" if name == "schema" else "")
            )
    found = [s for s, i in b.instances.items() if i.global_id == building and i.is_a("IfcBuilding")]
    if not found:
        raise MergeError(f"the model {other_id!r} has no building {building!r}")
    sites = [s for s, i in a.instances.items() if i.is_a("IfcSite") and (site is None or i.global_id == site)]
    if len(sites) != 1:
        raise MergeError(f"the model {base_id!r} has {len(sites)} sites that the building could go below, not one")
    site_placement = _placement_of(a.instances[sites[0]])
    if site_placement is None:
        raise MergeError("A's site has no placement")
    units = await _units(graph, a, b)
    contexts: dict[Any, int] = {}  # what B's contexts and applications become, when A has the same
    for step in sorted(a.instances):
        key = _context_key(a, step)
        if key is not None:
            contexts.setdefault(key, step)
        for unique in _application_keys(a, step):
            contexts.setdefault(unique, step)
    first = _select(b, found[0], site_placement, contexts, {}, extra=units)
    by_id = {i.global_id: s for s, i in a.instances.items() if i.global_id is not None}
    pairs = [(x, by_id[g]) for x in sorted(first.chosen) if (g := b.instances[x].global_id) is not None and g in by_id]
    await _fetch(graph, a, _closure(a, [y for _x, y in pairs] + list(first.subst.values())))
    await _fetch(graph, b, _closure(b, [x for x, _y in pairs], first.subst))
    memo_a: dict[int, str] = {}
    fixed = {x: _content(a, y, memo_a, {}) for x, y in first.subst.items()}
    memo_b: dict[int, str] = {}
    collisions, rows = [], []
    for x, y in pairs:
        same = _content(b, x, memo_b, fixed) == _content(a, y, memo_a, {})
        global_id = b.instances[x].global_id or ""
        choice = choices.get(global_id) or ("a" if same else default)
        collisions.append((x, y, choice))
        rows.append({"global_id": global_id, "ifc_class": b.props[x].get("ifc_class"), "same": same, "choice": choice})
    await _fetch(graph, b, first.left_out)
    report = {
        "base": base_id,
        "other": other_id,
        "building": building,
        "site": a.instances[sites[0]].global_id,
        "objects": len(first.objects),
        "instances": len(first.chosen),
        "collisions": rows,
        "left_out": [
            {"global_id": b.instances[s].global_id, "ifc_class": b.props[s].get("ifc_class")} for s in first.left_out
        ],
        "contexts": {"shared": sum(1 for s in first.subst if _context_key(b, s)), "added": len(first.new_contexts)},
        "placements": first.placements,
        "units_added": [_unit_name(b.props[unit]) for unit in units],
    }
    return _Plan(a, b, found[0], sites[0], site_placement, contexts, collisions, report, units)


_IMPERIAL = ("thou", "inch", "foot", "yard", "mile")


def _length_unit_name(props: dict[str, Any]) -> str:
    """A length unit as ``convert_file_length_units`` names it, such as ``MILLIMETER`` or ``foot``."""
    if props.get("ifc_class") == "IfcSIUnit" and props.get("Name") == "METRE":
        return (props.get("Prefix") or "") + "METER"
    name = str(props.get("Name") or "").lower()
    if props.get("ifc_class") == "IfcConversionBasedUnit" and name in _IMPERIAL:
        return name
    raise MergeError(f"A's length unit, {props.get('Prefix') or ''}{props.get('Name')}, is not one lengths convert to")


async def prepare_other(
    graph: Graph, other_id: str, model_id: str, *, base_id: str, limits: Limits = DEFAULT_LIMITS
) -> dict[str, Any]:
    """Make the model ``other_id`` ready to be merged into ``base_id``, as the new model ``model_id``: migrated to the
    base's schema, and with its lengths in the base's length unit, by IfcOpenShell in a worker, which needs the extra
    ``ifcopenshell``. Any other unit that A has another of is refused with what it is, for it is not converted; one
    that A has none of comes along in the merge. Returns what was
    done, with the instances whose class changed, and the new model's properties. The other model does not change."""
    a, b = await _load(graph, base_id), await _load(graph, other_id)
    base = {meaning[:2]: (unit, meaning) for unit, meaning in await _unit_meanings(graph, a)}
    other = {meaning[:2]: (unit, meaning) for unit, meaning in await _unit_meanings(graph, b)}
    length = ("LENGTHUNIT", None)
    differing = [
        _unit_name(b.props[unit])
        for kind, (unit, meaning) in sorted(other.items(), key=repr)
        if kind != length and kind in base and base[kind][1] != meaning
    ]
    if differing:
        raise MergeError(f"B has units other than A's, which are not converted: {'; '.join(differing)}")
    command = [sys.executable, "-m", "ifc_lpg.prepare"]
    options: list[str] = []
    if a.fields["schema"] != b.fields["schema"]:
        options += ["--schema", str(a.fields["schema"])]
    if length in base and length in other and base[length][1] != other[length][1]:
        options += ["--length-unit", _length_unit_name(a.props[base[length][0]])]
    if not options:
        raise MergeError("B has the schema and the length unit of A already; there is nothing to prepare")
    geometry = b.fields["geometry"]
    with tempfile.TemporaryDirectory(prefix="ifc-lpg-") as tmp:
        source, target = Path(tmp) / "other.ifc", Path(tmp) / "prepared.ifc"
        await export_file(graph, other_id, source, limits=limits)
        command += [str(source), str(target), *options, "--memory-mb", str(limits.memory_mb)]
        answer = json.loads((await run_worker(command, "preparing the model", limits.timeout_seconds)).splitlines()[-1])
        model = await import_file(
            graph,
            target,
            model_id,
            include_geometry=geometry == "nodes",
            geometry_in_blocks=geometry == "blocks",
            limits=limits,
        )
    return {**answer, "model": model}


async def merge_report(
    graph: Graph,
    base_id: str,
    other_id: str,
    building: str,
    *,
    site: str | None = None,
    choices: dict[str, str] | None = None,
    default: str | None = None,
) -> dict[str, Any]:
    """What bringing the building ``building`` (a GlobalId) of ``other_id`` into the project of ``base_id`` would do.

    The report counts what lies below the building (``objects``) and the instances of B that would come along; lists the
    objects with a GlobalId that A has too, with ``same`` for the same content and the ``choice`` that holds for it,
    ``None`` while none is made; lists the relationships left out because they point outside the building alone; and
    counts the contexts A has the same of and the ones added, and the placements outside the building that A's site's
    stands in for. ``site`` is the GlobalId of A's site to go below, if A has more than one."""
    plan = await _plan(graph, base_id, other_id, building, site, choices or {}, default)
    return plan.report


async def _create_nodes(graph: Graph, source: str, target: str, rows: list[tuple[int, int, frozenset[str]]]) -> None:
    """Copy the nodes ``(step in source, step in target, labels)`` in the database, by their labels."""
    groups: dict[tuple[str, ...], list[dict[str, int]]] = {}
    for old, new, labels in rows:
        groups.setdefault(tuple(sorted(labels - {"Entity"})), []).append({"old": old, "new": new})
    for labels, members in groups.items():
        clause = ":".join(["Entity", *map(safe_identifier, labels)])
        for start in range(0, len(members), BATCH):
            await graph.run(
                f"UNWIND $rows AS row MATCH (n:Entity {{id: $source + ':' + toString(row.old)}}) CREATE (c:{clause}) "
                f"SET c = n {{.*, id: $target + ':' + toString(row.new), step_id: row.new, model_id: $target}} "
                f"{_DERIVED}",
                rows=members[start : start + BATCH],
                source=source,
                target=target,
            )


async def _create_edges(graph: Graph, model_id: str, edges: Iterable[tuple[int, str, Any, int]]) -> None:
    """Relationships ``(from, type, position, to)`` between nodes of the model; no position is an inverse one."""
    groups: dict[tuple[str, bool], list[dict[str, Any]]] = {}
    for a, rel_type, position, b in edges:
        row: dict[str, Any] = {"a": f"{model_id}:{a}", "b": f"{model_id}:{b}"}
        if position is not None:
            row["position"] = position
        groups.setdefault((safe_identifier(rel_type), position is not None), []).append(row)
    for (rel_type, positioned), rows in groups.items():
        relationship = f"{rel_type} {{position: row.position}}" if positioned else rel_type
        for start in range(0, len(rows), BATCH):
            await graph.run(
                f"UNWIND $rows AS row MATCH (a:Entity {{id: row.a}}), (b:Entity {{id: row.b}}) "
                f"CREATE (a)-[:{relationship}]->(b)",
                rows=rows[start : start + BATCH],
            )


async def _copy_base(graph: Graph, a: _Model, model_id: str) -> None:
    """A in C as it is, in the database: nodes, relationships and blocks."""
    nodes = sorted(s for s, i in a.instances.items() if not i.in_block)
    await _create_nodes(graph, a.id, model_id, [(s, s, a.instances[s].labels) for s in nodes])
    for start in range(0, len(nodes), BATCH):
        rows = await graph.run(
            "UNWIND $ids AS id MATCH (n:Entity {id: id})-[r]->(t:Entity {model_id: $model}) "
            "RETURN n.step_id AS a, type(r) AS type, r.position AS position, t.step_id AS b",
            ids=[f"{a.id}:{s}" for s in nodes[start : start + BATCH]],
            model=a.id,
        )
        await _create_edges(graph, model_id, ((r["a"], r["type"], r["position"], r["b"]) for r in rows))
    firsts = await graph.run(
        "MATCH (:Model {id: $id})-[:HAS_STEP_BLOCK]->(b:StepBlock) RETURN b.id AS id ORDER BY b.first_step", id=a.id
    )
    for start in range(0, len(firsts), BLOCK_BATCH):
        await graph.run(
            "MATCH (m:Model {id: $model}) UNWIND $ids AS id MATCH (b:StepBlock {id: id}) "
            "CREATE (m)-[:HAS_STEP_BLOCK]->(:StepBlock {id: $model + ':' + toString(b.first_step), "
            "first_step: b.first_step, instances: b.instances, text: b.text})",
            model=model_id,
            ids=[row["id"] for row in firsts[start : start + BLOCK_BATCH]],
        )


def _edges_after(
    instance: _Instance, dropped: set[int]
) -> tuple[list[tuple[str, Any, int]], dict[str, dict[int, Any]]]:
    """An instance's forward relationships with ``dropped`` taken out, and each list's old positions to its new ones."""
    kept: list[tuple[str, Any, int]] = []
    moved: dict[str, dict[int, Any]] = {}
    groups: dict[str, list[tuple[Any, int]]] = {}
    for rel_type, position, target in instance.edges:
        groups.setdefault(rel_type, []).append((position, target))
    for rel_type, members in groups.items():
        if not any(target in dropped for _p, target in members):
            kept.extend((rel_type, position, target) for position, target in members)
            continue
        if not all(isinstance(position, int) for position, _t in members):
            raise MergeError(f"#{instance.step} has a list of lists to take a part of, which is not supported")
        positions: dict[int, Any] = {}
        for position, target in sorted(members):
            if target in dropped:
                positions[position] = None
            else:
                positions[position] = len(positions) - sum(1 for p in positions.values() if p is None)
                kept.append((rel_type, positions[position], target))
        moved[rel_type] = positions
    return kept, moved


async def merge_building(
    graph: Graph,
    base_id: str,
    other_id: str,
    building: str,
    model_id: str,
    *,
    site: str | None = None,
    choices: dict[str, str] | None = None,
    default: str | None = None,
    check: bool = True,
    limits: Limits = DEFAULT_LIMITS,
) -> dict[str, Any]:
    """Bring the building ``building`` (a GlobalId) of ``other_id`` into the project of ``base_id``, as the new model
    ``model_id``. ``choices`` maps a GlobalId that both have to ``"a"``, ``"b"`` or ``"new"``, and ``default`` holds for
    every other one whose content differs; :class:`MergeConflict` is raised while one has no choice, with the report.

    With ``check`` (the default), C is written out and validated against A and B before it is done, which needs the
    extra ``ifcopenshell``; without it, the check is the caller's. Returns the report with the model's properties."""
    plan = await _plan(graph, base_id, other_id, building, site, choices or {}, default)
    if any(choice is None for _x, _y, choice in plan.collisions):
        raise MergeConflict(plan.report)
    a, b = plan.a, plan.b
    mapped = {x: y for x, y, choice in plan.collisions if choice == "a"}
    replaced = {x: y for x, y, choice in plan.collisions if choice == "b"}
    renamed = {x for x, _y, choice in plan.collisions if choice == "new"}
    placements = {  # a product that is A's in C is placed where A places it, so that B's placement of it is A's
        pb: pa
        for x, y in mapped.items()
        if (pb := _placement_of(b.instances[x])) is not None and (pa := _placement_of(a.instances[y])) is not None
    }
    selection = _select(b, plan.building, plan.site_placement, plan.contexts, mapped, placements, extra=plan.units)
    rows = await graph.run(
        "MERGE (m:Model {id: $id}) ON CREATE SET m.format = 'step', m.form_version = $form, m.state = 'running', "
        "m.running_at = $now, m.schema = $schema, m.merge_of = [$base, $other], m.merge_building = $building "
        "RETURN m.running_at = $now AS new",
        id=model_id,
        schema=a.fields["schema"],
        form=FORM_VERSION,
        now=_now(),
        base=base_id,
        other=other_id,
        building=building,
    )
    if not rows or not rows[0]["new"]:
        raise ValueError(f"the model {model_id!r} exists already")
    try:
        counts = await _build(graph, plan, selection, model_id, replaced, renamed)
        totals = await graph.run(
            "MATCH (n:Entity {model_id: $id}) OPTIONAL MATCH (n)-[r]->(:Entity) RETURN count(DISTINCT n) AS nodes, "
            "count(r) AS relationships",
            id=model_id,
        )
        fields = {
            "schema": a.fields["schema"],
            "ifc_project_guid": a.fields["ifc_project_guid"],
            "entities": totals[0]["nodes"],
            "relationships": totals[0]["relationships"],
            "skipped": (a.fields["skipped"] or 0) + counts["in_blocks"],
            "lossless": True,
            "geometry": a.fields["geometry"],
            "ifc_header": a.fields["ifc_header"],
        }
        await graph.run("MATCH (m:Model {id: $id}) SET m += $fields", id=model_id, fields=fields)
        if check:
            await _check(graph, plan, model_id, counts["numbers"], limits)
    except Exception as exc:
        with contextlib.suppress(Exception):
            await delete_model(graph, model_id, keep_model=True)
        await graph.run(
            "MATCH (m:Model {id: $id}) SET m.state = 'failed', m.failed_at = $now, m.error = $error",
            id=model_id,
            now=_now(),
            error=f"{type(exc).__name__}: {exc}"[:500],
        )
        raise
    fields["done_at"] = _now()
    await graph.run(
        "MATCH (m:Model {id: $id}) SET m.state = 'done', m.done_at = $now", id=model_id, now=fields["done_at"]
    )
    return {**plan.report, "model": {"id": model_id, "state": "done", **fields}}


async def _build(
    graph: Graph, plan: _Plan, selection: _Selection, model_id: str, replaced: dict[int, int], renamed: set[int]
) -> dict[str, Any]:
    a, b = plan.a, plan.b
    await _copy_base(graph, a, model_id)
    after = max(a.instances) + 1
    numbers = {step: after + index for index, step in enumerate(sorted(selection.chosen))}
    spare = after + len(numbers)

    def to(step: int) -> int:
        nonlocal spare
        if step in selection.subst:
            return selection.subst[step]
        if step not in numbers:  # a reference to nothing stays one
            numbers[step] = spare
            spare += 1
        return numbers[step]

    nodes = [s for s in sorted(selection.chosen) if not b.instances[s].in_block]
    await _create_nodes(graph, b.id, model_id, [(s, numbers[s], b.instances[s].labels) for s in nodes])
    edges: list[tuple[int, str, Any, int]] = []
    patches: list[dict[str, Any]] = []
    owner = _owner_of_base(a)
    for step in nodes:
        instance, dropped = b.instances[step], selection.drop.get(step, set())
        kept, moved = _edges_after(instance, dropped)
        edges.extend((numbers[step], rel_type, position, to(target)) for rel_type, position, target in kept)
        props: dict[str, Any] = {}
        if owner is not None and instance.global_id is not None and not _has_owner(instance):
            props["OwnerHistory" + STEP_SUFFIX] = owner  # IFC2X3 has one on every rooted instance
        for key, value in instance.steps.items():
            props[key] = [to(t) for t in value if t not in dropped] if isinstance(value, list) else to(value)
        for key, text in instance.tagged.items():
            rel_type = "ARGUMENT" if key == ALL_ARGUMENTS else relationship_type(key)
            props[key] = json.dumps(_map_tagged(json.loads(text), to, dropped, moved.get(rel_type, {})))
        if step in renamed:
            props["GlobalId"] = new_global_id()
        if props:
            patches.append({"id": f"{model_id}:{numbers[step]}", "props": props})
    for start in range(0, len(patches), BATCH):
        await graph.run(
            "UNWIND $rows AS row MATCH (c:Entity {id: row.id}) SET c += row.props", rows=patches[start : start + BATCH]
        )
    lines = []
    for step in sorted(selection.chosen, key=lambda s: numbers[s]):
        instance = b.instances[step]
        if instance.parameters is not None:
            parameters = _map_parameters(instance.parameters, to, selection.drop.get(step, set()))
            lines.append(b"#%d=%s;\n" % (numbers[step], statement_text(instance.keyword, parameters)))
    await _create_blocks(graph, model_id, lines)
    created = [numbers[s] for s in nodes]
    if selection.upward is not None and plan.building not in replaced:
        created.append(await _below_site(graph, plan, selection, model_id, spare, to))
        spare += 1
    building = {y for x, y in replaced.items() if x == plan.building}  # B's takes A's place below the site
    moved = {  # A's placement of a replaced product, to B's placement of it in C
        pa: numbers[pb]
        for x, y in replaced.items()
        if (pa := _placement_of(a.instances[y])) is not None
        and (pb := _placement_of(b.instances[x])) is not None
        and pb in numbers
        and pa not in selection.subst.values()
    }
    shared = {y for _x, y, choice in plan.collisions if choice == "a" and a.instances[y].is_a("IfcRelationship")}
    changed, gone_from_blocks = await _put_in_place(
        graph, a, model_id, replaced, numbers, set(selection.subst.values()), building, moved, shared
    )
    created += changed
    created += await _join(graph, plan, model_id, numbers)
    created += await _add_units(graph, plan, model_id, numbers)
    created += await _add_contexts(graph, a, model_id, [to(s) for s in selection.new_contexts])
    await _create_edges(graph, model_id, edges)
    await refresh_inverses(graph, model_id, sorted(set(created)))
    await graph.run(
        "MATCH (m:Model {id: $id}), (p:Entity:IfcProject {model_id: $id}) MERGE (m)-[:HAS_PROJECT]->(p)", id=model_id
    )
    return {"numbers": numbers, "in_blocks": len(lines) - gone_from_blocks}


async def _join(graph: Graph, plan: _Plan, model_id: str, numbers: dict[int, int]) -> list[int]:
    """What of B is in C as its own joins the relationships of A that B has as well: a new wall the property sets, the
    type and the material it shares with A's, as B's relationship relates it there. It is appended to the lists of A's
    relationship, since IFC2X3 lets a property set or a type have only one. Returns the relationships that changed."""
    lists = list_attributes(str(plan.a.fields["schema"]))
    rows: list[tuple[int, str, Any, int]] = []
    changed = []
    for x, y, choice in plan.collisions:
        relation = plan.b.instances[x]
        if choice != "a" or not relation.is_a("IfcRelationship"):
            continue
        for declaring, attribute in lists:
            if not relation.is_a(declaring):
                continue
            rel_type = relationship_type(attribute)
            members = [
                target for kind, _p, target in sorted(relation.edges, key=lambda e: str(e[1])) if kind == rel_type
            ]
            joining = [numbers[target] for target in members if target in numbers]
            if not joining:
                continue
            found = await graph.run(
                f"MATCH (:Entity {{id: $id}})-[e:{safe_identifier(rel_type)}]->(t:Entity) WHERE e.position IS NOT NULL "
                "RETURN t.step_id AS step",
                id=f"{model_id}:{y}",
            )
            there = {row["step"] for row in found}
            new = [step for step in joining if step not in there]
            rows += [(y, rel_type, len(there) + index, step) for index, step in enumerate(new)]
            changed += [y] if new else []
    await _create_edges(graph, model_id, rows)
    return changed


async def _create_blocks(graph: Graph, model_id: str, lines: list[bytes]) -> None:
    blocks: list[dict[str, Any]] = []
    current: list[bytes] = []
    first = 0
    for line in lines:
        if not current:
            first = int(line[1 : line.index(b"=")])
        current.append(line)
        if sum(map(len, current)) >= BLOCK_BYTES:
            blocks.append({"first": first, "instances": len(current), "text": encode_block(b"".join(current))})
            current = []
    if current:
        blocks.append({"first": first, "instances": len(current), "text": encode_block(b"".join(current))})
    for start in range(0, len(blocks), BLOCK_BATCH):
        await graph.run(
            "MATCH (m:Model {id: $id}) UNWIND $rows AS row CREATE (m)-[:HAS_STEP_BLOCK]->(:StepBlock {id: $id + ':' + "
            "toString(row.first), first_step: row.first, instances: row.instances, text: row.text})",
            id=model_id,
            rows=blocks[start : start + BLOCK_BATCH],
        )


async def _below_site(
    graph: Graph, plan: _Plan, selection: _Selection, model_id: str, step: int, to: Callable[[int], int]
) -> int:
    """The aggregation that puts the building below A's site: B's, with a new GlobalId and the building's owner."""
    upward = plan.b.instances[selection.upward or 0]
    await _fetch(graph, plan.b, [upward.step])
    props = {
        key: value
        for key, value in plan.b.props[upward.step].items()
        if key in ("ifc_class", "Name", "Description", "Name__type", "Description__type")
    }
    props["GlobalId"] = new_global_id()
    building = plan.b.instances[plan.building]
    owner = building.attribute("OwnerHistory") or [building.steps.get("OwnerHistory" + STEP_SUFFIX)]
    owned_by_a = False
    if owner[0] is None:  # the base project's, which IFC2X3 has, so that the aggregation has one as well
        project = plan.a.instances[plan.a.single("IfcProject")]
        owner = project.attribute("OwnerHistory") or [project.steps.get("OwnerHistory" + STEP_SUFFIX)]
        owned_by_a = owner[0] is not None
    if owner[0] is not None:  # by its STEP id, as every instance but the project holds its owner history
        props["OwnerHistory" + STEP_SUFFIX] = owner[0] if owned_by_a else to(owner[0])
    clause = ":".join(["Entity", *map(safe_identifier, sorted(upward.labels - {"Entity"}))])
    await graph.run(
        f"CREATE (c:{clause}) SET c = $props, c.id = $id + ':' + toString($step), c.step_id = $step, c.model_id = $id",
        props=props,
        id=model_id,
        step=step,
    )
    edges = [(step, "RELATING_OBJECT", 0, plan.site), (step, "RELATED_OBJECTS", 0, to(plan.building))]
    await _create_edges(graph, model_id, edges)
    return step


def _held_by(a: _Model, gone: set[int], keep: set[int]) -> tuple[set[int], dict[int, set[int]]]:
    """What the objects ``gone`` alone hold in A, on to the styles and layers of it, and the attached instances that
    keep a part of what they belong to, with the part that goes. Nothing in ``keep`` goes."""
    attached = {s: set(found) for s, i in a.instances.items() if (found := _anchors(i)) is not None}
    dropped, frontier = set(gone), list(gone)
    while frontier:
        found: set[int] = set()
        for step in frontier:
            for target in a.instances[step].targets():
                instance = a.instances.get(target)
                if target in dropped | found | keep or instance is None or instance.global_id is not None:
                    continue
                referrers = a.referrers.get(target, ())
                if all(r in dropped or r in found or target in attached.get(r, ()) for r in referrers):
                    found.add(target)
        for step, anchors in attached.items():
            if step not in dropped | found | keep and anchors and anchors <= dropped | found:
                found.add(step)
        dropped |= found
        frontier = list(found)
    splits = {s: anchors & dropped for s, anchors in attached.items() if s not in dropped and anchors & dropped}
    return dropped, splits


async def _rewrite_blocks(
    graph: Graph, model_id: str, dropped: set[int], splits: dict[int, set[int]], instead: dict[int, int]
) -> int:
    """C's blocks without the instances ``dropped``, with ``splits`` taken out of their lists, and with every reference
    in ``instead`` to what takes its place. Returns how many instances went."""
    gone = 0
    for row in await graph.run(
        "MATCH (:Model {id: $id})-[:HAS_STEP_BLOCK]->(b:StepBlock) RETURN b.id AS id, b.text AS text", id=model_id
    ):
        lines, changed = [], False
        for step, statement in statements_of(decode_block(row["text"])):
            if step in dropped:
                changed, gone = True, gone + 1
                continue
            keyword, parameters = _parse(statement)
            if step in splits or set(_ids(parameters)) & set(instead):
                parameters = _map_parameters(
                    parameters, lambda target: instead.get(target, target), splits.get(step, set())
                )
                statement, changed = b"#%d=%s;" % (step, statement_text(keyword, parameters)), True
            lines.append(statement + b"\n")
        if changed and lines:
            await graph.run(
                "MATCH (b:StepBlock {id: $id}) SET b.text = $text, b.instances = $instances",
                id=row["id"],
                text=encode_block(b"".join(lines)),
                instances=len(lines),
            )
        elif changed:
            await graph.run("MATCH (b:StepBlock {id: $id}) DETACH DELETE b", id=row["id"])
    return gone


async def _put_in_place(
    graph: Graph,
    a: _Model,
    model_id: str,
    replaced: dict[int, int],
    numbers: dict[int, int],
    keep: set[int],
    placed: set[int],
    moved: dict[int, int],
    shared: set[int],
) -> tuple[list[int], int]:
    """B's version in place of A's: what of A refers to it refers to B's, a relationship that concerns only replaced
    objects goes, and one that also concerns others keeps those; what A's held alone goes with it. A replaced object
    leaves A's structure for B's, except the building, which B's has no structure above: B's takes its place in A's.
    What A placed relative to a replaced product is placed relative to B's, as ``moved`` says, and A's placement goes.
    A relationship that B has as well, ``shared``, stays as it is and relates B's version. Returns the nodes whose relationships changed, and how many instances went from the blocks."""
    if not replaced:
        return [], 0
    gone = set(replaced.values())
    instead = {y: numbers[x] for x, y in replaced.items()} | moved
    referring = {r for target in instead for r in a.referrers.get(target, ())} - gone
    removed: set[int] = set()
    rel_drops: dict[int, set[int]] = {}
    leaving = gone - placed  # what leaves A's structure
    for step in sorted(referring):
        instance = a.instances[step]
        if not instance.is_a("IfcRelationship") or step in shared:
            continue
        others = [
            t
            for t in instance.targets()
            if t not in gone
            and a.instances.get(t) is not None
            and a.instances[t].global_id is not None
            and not any(map(a.instances[t].is_a, _DEFINITIONS))
        ]
        groups: dict[str, list[int]] = {}
        for rel_type, _position, target in instance.edges:
            groups.setdefault(rel_type, []).append(target)
        related = _related_of(instance)
        if not others or (related in groups and set(groups[related]) <= leaving):
            removed.add(step)
            continue
        rel_drops[step] = {
            t
            for rel_type, members in groups.items()
            for t in members
            if t in gone and ((rel_type == related and t in leaving) or any(m not in gone for m in members))
        }
    held, splits = _held_by(a, gone | removed | set(moved), keep)  # and what the relationships that go held alone
    rewritten: list[tuple[int, str, Any, int]] = []
    patches: list[dict[str, Any]] = []
    changed: set[int] = set()

    async def rewrite(step: int, parts: set[int]) -> None:
        instance = a.instances[step]
        kept, _moved = _edges_after(instance, parts)
        touched = {rel_type for rel_type, _p, target in instance.edges if target in instead or target in parts}
        for rel_type in touched:
            await graph.run(
                f"MATCH (n:Entity {{id: $id}})-[r:{safe_identifier(rel_type)}]->() WHERE r.position IS NOT NULL DELETE r",
                id=f"{model_id}:{step}",
            )
        rewritten.extend(
            (step, rel_type, position, instead.get(target, target))
            for rel_type, position, target in kept
            if rel_type in touched
        )
        if any(set(_tagged_ids(json.loads(text))) & (parts | set(instead)) for text in instance.tagged.values()):
            raise MergeError(f"#{step} of A refers to what changes in a form that cannot be changed here")
        props = {
            key: [instead.get(v, v) for v in value if v not in parts]
            if isinstance(value, list)
            else instead.get(value, value)
            for key, value in instance.steps.items()
            if set(value if isinstance(value, list) else [value]) & (parts | set(instead))
        }
        if props:
            patches.append({"id": f"{model_id}:{step}", "props": props})
        changed.add(step)

    for step in sorted(referring - held - removed):
        if not a.instances[step].in_block:
            await rewrite(step, rel_drops.get(step, set()))
    for step, parts in sorted(splits.items()):
        if not a.instances[step].in_block and step not in changed:
            await rewrite(step, parts)
    for start in range(0, len(patches), BATCH):
        await graph.run(
            "UNWIND $rows AS row MATCH (c:Entity {id: row.id}) SET c += row.props", rows=patches[start : start + BATCH]
        )
    in_blocks = {s for s in held if a.instances[s].in_block}
    nodes = sorted((held - in_blocks) | removed)
    for start in range(0, len(nodes), BATCH):
        await graph.run(
            "UNWIND $ids AS id MATCH (n:Entity {id: id}) DETACH DELETE n",
            ids=[f"{model_id}:{s}" for s in nodes[start : start + BATCH]],
        )
    block_splits = {s: parts for s, parts in splits.items() if a.instances[s].in_block}
    from_blocks = any(a.instances[s].in_block for s in referring - held)
    gone_from_blocks = (
        await _rewrite_blocks(graph, model_id, in_blocks, block_splits, instead)
        if in_blocks or block_splits or from_blocks
        else 0
    )
    await _create_edges(graph, model_id, rewritten)
    return sorted(changed - held - removed), gone_from_blocks


async def _add_units(graph: Graph, plan: _Plan, model_id: str, numbers: dict[int, int]) -> list[int]:
    """B's units of a kind that A has none of, in A's ``IfcUnitAssignment``."""
    assignment = _assignment(plan.a)
    if not plan.units or assignment is None:
        return []
    there = len(plan.a.instances[assignment].attribute("Units"))
    edges = [(assignment, "UNITS", there + i, numbers[unit]) for i, unit in enumerate(plan.units)]
    await _create_edges(graph, model_id, edges)
    return [assignment]


async def _add_contexts(graph: Graph, a: _Model, model_id: str, contexts: list[int]) -> list[int]:
    """The root contexts that A did not have, in its project's ``RepresentationContexts``."""
    if not contexts:
        return []
    project = a.single("IfcProject")
    instance = a.instances[project]
    existing = instance.attribute("RepresentationContexts")
    key = "RepresentationContexts" + STEP_SUFFIX
    if key in instance.steps:
        value = instance.steps[key]
        listed = value if isinstance(value, list) else [value]
        await graph.run(
            "MATCH (n:Entity {id: $id}) SET n.RepresentationContexts__step = $value",
            id=f"{model_id}:{project}",
            value=listed + contexts,
        )
        return []
    if not existing and "RepresentationContexts" in instance.tagged:
        raise MergeError("A's project holds its contexts in a form that cannot take more")
    edges = [(project, "REPRESENTATION_CONTEXTS", len(existing) + i, step) for i, step in enumerate(contexts)]
    await _create_edges(graph, model_id, edges)
    return [project]


def _renumbered(records: Path, out: Path, to: Callable[[int], int]) -> None:
    """Exported records with every STEP id through ``to``, in the new order."""
    lines: list[tuple[int, str]] = []
    with records.open(encoding="utf-8") as handle:
        header = next(handle)
        for line in handle:
            record = json.loads(line)
            step = to(record["step"])
            if "line" in record:
                keyword, parameters = _parse(record["line"].encode("latin-1"))
                text = b"#%d=%s;" % (step, statement_text(keyword, _map_step(parameters, to)))
                lines.append((step, json.dumps({"step": step, "line": text.decode("latin-1")})))
                continue
            props = record["props"]
            for key, value in list(props.items()):
                if key.endswith(STEP_SUFFIX):
                    props[key] = [to(t) for t in value] if isinstance(value, list) else to(value)
                elif key == ALL_ARGUMENTS or props.get(key + TYPE_SUFFIX) == TAGGED:
                    props[key] = json.dumps(_map_tagged(json.loads(value), to, set(), {}))
            references = [[rel_type, position, to(target)] for rel_type, position, target in record["references"]]
            lines.append((step, json.dumps({"step": step, "props": props, "references": references})))
    with out.open("w", encoding="utf-8") as handle:
        handle.write(header)
        handle.writelines(text + "\n" for _step, text in sorted(lines))


async def _check(graph: Graph, plan: _Plan, model_id: str, numbers: dict[int, int], limits: Limits) -> None:
    """C is validated against A and B, B numbered as C numbers it, and must have no issue that they do not have."""
    spare = max(numbers.values(), default=0) + 2
    full = dict(numbers)
    for step in sorted(plan.b.instances):
        if step not in full:
            full[step] = spare
            spare += 1
    with tempfile.TemporaryDirectory(prefix="ifc-lpg-") as tmp:
        folder = Path(tmp)
        await graph.run("MATCH (m:Model {id: $id}) SET m.state = 'done'", id=model_id)  # for the export alone
        try:
            await export_file(graph, model_id, folder / "c.ifc", limits=limits)
        finally:
            await graph.run("MATCH (m:Model {id: $id}) SET m.state = 'running'", id=model_id)
        await export_file(graph, plan.a.id, folder / "a.ifc", limits=limits)
        await export_records(graph, plan.b.id, folder / "b.jsonl")
        _renumbered(folder / "b.jsonl", folder / "b2.jsonl", lambda step: full.setdefault(step, step + spare))
        await write_file(folder / "b2.jsonl", folder / "b.ifc", limits=limits)
        command = [sys.executable, "-m", "ifc_lpg.validation", str(folder / "c.ifc"), "--against"]
        command += [str(folder / "a.ifc"), str(folder / "b.ifc"), "--memory-mb", str(limits.memory_mb)]
        answer = await run_worker(command, "the check of the merged model", limits.timeout_seconds, answers=(0, 1))
    result = json.loads(answer)
    if result["new_count"]:
        messages = "; ".join(f"#{issue['instance']} {issue['message'][:200]}" for issue in result["new"][:5])
        raise MergeError(f"the merged model has {result['new_count']} issues that A and B do not have: {messages}")
