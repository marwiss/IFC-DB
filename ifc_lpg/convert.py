# This file was generated with the assistance of an AI coding tool.
"""Convert an IFC file into node and edge records (runs inside the worker process).

Two forms come out of here. :func:`convert_lossless` is the default, and a file can be written back from what it gives.
:func:`convert` is the ordinary form, without ``--lossless``, which the rest of this text is about, and whose
nodes, relationships and values are all in the other one too.

Every entity becomes a node labelled with its class and all its supertypes
(``IfcWallStandardCase``, ``IfcWall``, ``IfcBuildingElement``, …, ``IfcRoot``)
plus ``Entity``. Literal attributes become properties; attributes that point
at other entities become relationships named after the attribute, and so do
inverse attributes (``IsDefinedBy``, ``ContainsElements``), which is what makes
queries such as the GTIN lookup readable. Geometry (representation items,
profiles, placements, styles) is left out unless asked for; it is most of a
model and rarely what a CDE query needs. ``IfcOwnerHistory`` is kept only for
the ``IfcProject``.

The mapping follows Zhu, Wu and Lei, "IFC-graph for facilitating building
information access and query", Automation in Construction 148 (2023), 104778,
https://doi.org/10.1016/j.autcon.2023.104778; the README says where
this code differs from the article.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ifc_lpg.lossless import as_points, encode_block, encode_header, encode_instance
from ifc_lpg.schema import Schema, point_size
from ifc_lpg.step import StepError, StepFile, statement_text

GEOMETRY_ROOTS = (
    "IfcRepresentationItem",
    "IfcRepresentation",
    "IfcProductRepresentation",
    "IfcRepresentationContext",
    "IfcProfileDef",
    "IfcPresentationStyle",
    "IfcPresentationStyleAssignment",
    "IfcPresentationLayerAssignment",
    "IfcObjectPlacement",
    "IfcPlacement",
    "IfcPresentationItem",
)
# What stays text in the skeleton, the default form: the many small instances that shapes are built of. The rest of the
# geometry is nodes: representations, solids, profiles, mapped items, placements, styled items, styles, colours and
# layers, so that every style can be edited (Martin 2026-11-10).
BULK_GEOMETRY = (
    "IfcPoint",
    "IfcDirection",
    "IfcVector",
    "IfcCartesianPointList",
    "IfcTessellatedItem",
    "IfcTopologicalRepresentationItem",
    "IfcCurve",
    "IfcColourRgbList",
    "IfcTextureVertexList",
    "IfcTextureCoordinate",
    "IfcIndexedColourMap",
)
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
BLOCK_BYTES = 256 * 1024  # before compression; what a read of one instance's geometry would have to unpack


@dataclass
class Summary:
    schema: str = ""
    entities: int = 0
    relationships: int = 0
    skipped: int = 0
    ifc_project_guid: str | None = None
    classes: dict[str, int] = field(default_factory=dict)


class ConversionError(RuntimeError):
    pass


# Where a storey lies, for the files that leave its Elevation out, as buildingSMART's rule IFC102 has IFC4X3 files do.
# Derived values have snake_case names, which no IFC attribute has, so the writer leaves them out.
_PREFIX = {
    b".EXA.": 1e18, b".PETA.": 1e15, b".TERA.": 1e12, b".GIGA.": 1e9, b".MEGA.": 1e6, b".KILO.": 1e3, b".HECTO.": 1e2,
    b".DECA.": 1e1, b".DECI.": 1e-1, b".CENTI.": 1e-2, b".MILLI.": 1e-3, b".MICRO.": 1e-6, b".NANO.": 1e-9,
    b".PICO.": 1e-12, b".FEMTO.": 1e-15, b".ATTO.": 1e-18,
}  # fmt: skip
_Matrix = tuple[tuple[float, ...], ...]  # three rows of four: the axes as columns, and the origin last


def _real(value: Any) -> float | None:
    """A number of the reader's atoms, also inside a typed value such as ``IFCLENGTHMEASURE(0.3048)``."""
    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[1], list) and len(value[1]) == 1:
        value = value[1][0]
    if isinstance(value, bytes) and value[:1] == b"r":
        return float.fromhex(value[1:].decode("ascii"))
    if isinstance(value, bytes) and value[:1] == b"i":
        return float(int(value[1:]))
    return None


def storey_heights(file: StepFile, schema: Schema) -> dict[int, dict[str, Any]]:
    """For each ``IfcBuildingStorey``, ``elevation_m`` and ``elevation_source``: its Elevation in metres where the file
    gives one (``attribute``), and otherwise the height of its placement's origin in the project's coordinates
    (``placement``), through every local placement it is relative to, rotations included. A storey whose height cannot
    be worked out, and every storey of a file whose length unit is not known, gets neither."""

    def named(step: Any) -> dict[str, Any]:
        if not isinstance(step, int) or step not in file:
            return {}
        keyword, parameters = file[step]
        attributes = schema.attributes(keyword) or ()  # an instance of another length than its class has is not read
        return (
            dict(zip((a.name for a in attributes), parameters, strict=True))
            if len(attributes) == len(parameters)
            else {}
        )

    def keyword(step: Any) -> bytes:
        return file.keyword_of(step) if isinstance(step, int) and step in file else b""

    def metres(unit: Any, depth: int = 0) -> float | None:
        found = named(unit)
        if depth > 8 or not found:
            return None
        if keyword(unit) == b"IFCSIUNIT":
            return _PREFIX.get(found.get("Prefix"), 1.0) if found.get("Name") == b".METRE." else None
        measure = named(found.get("ConversionFactor"))
        value, inner = _real(measure.get("ValueComponent")), metres(measure.get("UnitComponent"), depth + 1)
        return value * inner if value is not None and inner is not None else None

    def numbers(step: Any, name: str) -> list[float] | None:
        found = [_real(item) for item in named(step).get(name) or ()] if isinstance(named(step).get(name), list) else []
        return [number for number in found if number is not None] if found and None not in found else None

    def axes(step: Any) -> _Matrix | None:
        found = named(step)
        origin = (
            numbers(found.get("Location"), "Coordinates")
            if keyword(found.get("Location")) == b"IFCCARTESIANPOINT"
            else None
        )
        if origin is None or keyword(step) not in (b"IFCAXIS2PLACEMENT3D", b"IFCAXIS2PLACEMENT2D"):
            return None
        origin = [*origin, 0.0, 0.0][:3]
        z = _unit((numbers(found.get("Axis"), "DirectionRatios") or [0.0, 0.0, 1.0]) + [0.0])
        x = _unit((numbers(found.get("RefDirection"), "DirectionRatios") or [1.0, 0.0, 0.0]) + [0.0])
        if z is None or x is None:
            return None
        along = sum(a * b for a, b in zip(x, z, strict=True))
        x = _unit([a - along * b for a, b in zip(x, z, strict=True)])
        if x is None:
            return None
        y = (z[1] * x[2] - z[2] * x[1], z[2] * x[0] - z[0] * x[2], z[0] * x[1] - z[1] * x[0])
        return tuple((x[row], y[row], z[row], origin[row]) for row in range(3))

    def placed(step: Any, seen: frozenset[int] = frozenset()) -> _Matrix | None:
        found = named(step)
        if keyword(step) != b"IFCLOCALPLACEMENT" or step in seen or len(seen) > 64:
            return None
        local = axes(found.get("RelativePlacement"))
        parent = found.get("PlacementRelTo")
        if local is None or not isinstance(parent, int):
            return local
        above = placed(parent, seen | {step})
        return _times(above, local) if above is not None else None

    projects = [step for step in file.ids() if file.keyword_of(step) == b"IFCPROJECT"]
    units = named(named(projects[0]).get("UnitsInContext")).get("Units") if len(projects) == 1 else None
    lengths = (
        [unit for unit in units if named(unit).get("UnitType") == b".LENGTHUNIT."] if isinstance(units, list) else []
    )
    scale = metres(lengths[0]) if len(lengths) == 1 else None
    heights: dict[int, dict[str, Any]] = {}
    if scale is None:
        return heights
    for step in file.ids():
        if file.keyword_of(step) != b"IFCBUILDINGSTOREY":
            continue
        storey = named(step)
        elevation = _real(storey.get("Elevation"))
        if elevation is not None:
            heights[step] = {"elevation_m": elevation * scale, "elevation_source": "attribute"}
            continue
        matrix = placed(storey.get("ObjectPlacement"))
        if matrix is not None:
            heights[step] = {"elevation_m": matrix[2][3] * scale, "elevation_source": "placement"}
    return heights


def _unit(vector: list[float]) -> tuple[float, ...] | None:
    length = sum(item * item for item in vector[:3]) ** 0.5
    return tuple(item / length for item in vector[:3]) if length > 1e-12 else None


def _times(a: _Matrix, b: _Matrix) -> _Matrix:
    """``a`` after ``b``: the placement ``b`` in the coordinates ``a`` places it in."""
    return tuple(
        tuple(
            sum(a[row][k] * b[k][column] for k in range(3)) + (a[row][3] if column == 3 else 0.0) for column in range(4)
        )
        for row in range(3)
    )


def _literal(value: Any, points: int = 0) -> Any:
    """A JSON-storable scalar or list, or ``None`` when the value is an entity (handled as an edge). ``points`` is the
    length of the rows when the schema says the value is a list of points."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if hasattr(value, "wrappedValue"):  # a select type such as IfcLabel('3')
        return _literal(value.wrappedValue)
    if isinstance(value, tuple | list):
        items = [_literal(item) for item in value]
        if any(item is None for item in items):
            return None
        if all(isinstance(item, bool | int | float | str) for item in items):
            return items
        marked = as_points(items, points)
        return marked if marked is not None else json.dumps(items)  # other nested lists as JSON text
    return None


def convert(
    path: Path, *, include_geometry: bool = False, geometry_in_blocks: bool = False, max_entities: int = 5_000_000
) -> Iterator[dict[str, Any]]:
    """Yield a header record, then node records, then edge records. The ordinary form leaves all geometry out unless it is
    asked to be nodes, so ``geometry_in_blocks`` changes nothing here."""
    try:
        import ifcopenshell
    except ImportError as exc:  # pragma: no cover - depends on the optional extra
        raise ConversionError("IfcOpenShell is not installed; install the 'ifc-lpg' extra") from exc

    model = ifcopenshell.open(str(path))
    schema = ifcopenshell.schema_by_name(model.schema)
    summary = Summary(schema=model.schema)
    try:
        heights = storey_heights(StepFile(path.read_bytes()), Schema(model.schema))
    except StepError:
        heights = {}
    label_cache: dict[str, list[str]] = {}
    point_cache: dict[str, dict[str, int]] = {}

    def labels_of(class_name: str) -> list[str]:
        if class_name not in label_cache:
            chain = []
            declaration: Any = schema.declaration_by_name(class_name)
            while declaration is not None:
                chain.append(declaration.name())
                declaration = declaration.supertype()
            label_cache[class_name] = [name for name in chain if _IDENTIFIER.match(name)]
        return label_cache[class_name]

    def points_of(class_name: str) -> dict[str, int]:
        if class_name not in point_cache:
            declaration: Any = schema.declaration_by_name(class_name)
            attributes = declaration.all_attributes()
            point_cache[class_name] = {item.name(): point_size(item.type_of_attribute()) for item in attributes}
        return point_cache[class_name]

    def skipped(entity: Any) -> bool:
        if entity.is_a("IfcOwnerHistory"):
            return True
        return not include_geometry and any(entity.is_a(root) for root in GEOMETRY_ROOTS)

    kept: set[int] = set()
    edges: list[dict[str, Any]] = []
    total = 0
    unreadable = 0
    for entity in model:
        total += 1
        if total > max_entities:
            raise ConversionError(f"the model has more than {max_entities} entities")
        if skipped(entity):
            summary.skipped += 1
            continue
        kept.add(entity.id())

    project = next(iter(model.by_type("IfcProject")), None)
    owner_history_id = project.OwnerHistory.id() if project is not None and project.OwnerHistory else None
    if owner_history_id is not None:
        kept.add(owner_history_id)
    summary.ifc_project_guid = project.GlobalId if project is not None else None
    project_id = project.id() if project is not None else None

    def linked(source: int, target: Any) -> bool:
        """Whether an edge goes in the graph. Every ``IfcRoot`` usually shares the project's owner history, and an
        edge from each of them would make that one node the centre of the graph."""
        if not hasattr(target, "id") or target.id() not in kept:
            return False
        return target.id() != owner_history_id or source == project_id

    for entity in model:
        step = entity.id()
        if step not in kept:
            continue
        class_name = entity.is_a()
        props: dict[str, Any] = {"ifc_class": class_name}
        points = points_of(class_name)
        for index, name in enumerate(entity.get_attribute_names()):
            if not _IDENTIFIER.match(name):
                continue
            try:
                value = entity[index]
            except Exception:
                unreadable += 1
                continue
            if value is None:
                continue
            literal = _literal(value, points.get(name, 0))
            if literal is not None:
                props[name] = literal
                continue
            for target in value if isinstance(value, tuple | list) else (value,):
                if linked(step, target):
                    edges.append({"t": "e", "a": step, "b": target.id(), "type": name})
        try:
            inverse_names = entity.get_inverse_attribute_names()
        except Exception:
            inverse_names = ()
        for name in inverse_names:
            if not _IDENTIFIER.match(name):
                continue
            try:
                targets = getattr(entity, name) or ()
            except Exception:
                unreadable += 1
                continue
            for target in targets:
                if linked(step, target):
                    edges.append({"t": "e", "a": step, "b": target.id(), "type": name})
        summary.entities += 1
        summary.classes[class_name] = summary.classes.get(class_name, 0) + 1
        yield {"t": "n", "step": step, "labels": labels_of(class_name), "props": {**props, **heights.get(step, {})}}

    summary.relationships = len(edges)
    yield {
        "t": "h",
        "schema": summary.schema,
        "entities": summary.entities,
        "relationships": summary.relationships,
        "skipped": summary.skipped,
        "ifc_project_guid": summary.ifc_project_guid,
        "classes": summary.classes,
    }
    yield from edges


def convert_lossless(
    path: Path,
    *,
    include_geometry: bool = False,
    geometry_in_blocks: bool = False,
    max_entities: int = 5_000_000,
) -> Iterator[dict[str, Any]]:
    """Yield the records of a graph a file can be written back from: nothing the file says is left out.

    It reads the file itself and uses IfcOpenShell only for the schema, because IfcOpenShell's reading changes some
    values (``step.py``) and its API cannot make all of them (``IFCPROPERTYSETDEFINITIONSET`` ends the process). See
    ``lossless.py`` for where each value goes, and ADR 0010. Inverse attributes are here for the queries' sake; they
    are worked out from the schema, and the writer does not read them.

    Three forms. The skeleton, the default, keeps only ``BULK_GEOMETRY`` in blocks: what shapes are built of, the points,
    the meshes, the curves. ``geometry_in_blocks`` keeps all geometry there, which leaves the nodes of the ordinary form,
    and ``include_geometry`` makes every instance a node. An owner history is linked from its project alone in each,
    since every ``IfcRoot`` usually shares it, and an edge from each of them would make that one node the centre of the
    graph.
    """
    if include_geometry and geometry_in_blocks:
        raise ValueError("geometry is either nodes or in blocks, not both")
    form = "nodes" if include_geometry else "blocks" if geometry_in_blocks else "skeleton"
    try:
        file = StepFile(path.read_bytes())
        if len(file) > max_entities:
            raise ConversionError(f"the model has more than {max_entities} entities")
        schema = Schema(file.schema)
        heights = storey_heights(file, schema)
        keywords = {step: file.keyword_of(step) for step in file.ids()}
        project = next((step for step, keyword in keywords.items() if keyword == b"IFCPROJECT"), None)
        project_history = _second_parameter(file, project)
    except StepError as exc:
        raise ConversionError(str(exc)) from exc

    classes = set(keywords.values())
    roots = set(GEOMETRY_ROOTS if form == "blocks" else BULK_GEOMETRY)
    geometry_classes = {keyword for keyword in classes if set(schema.chain(keyword)) & roots}
    history_classes = {keyword for keyword in classes if "IfcOwnerHistory" in schema.chain(keyword)}
    histories = {step for step, keyword in keywords.items() if keyword in history_classes}
    in_blocks: set[int] = set()
    if form != "nodes":
        in_blocks = {step for step, keyword in keywords.items() if keyword in geometry_classes}
        in_blocks |= histories - {project_history}
    nodes = keywords.keys() - in_blocks
    targets = nodes - histories
    project_targets = targets | ({project_history} & nodes)

    summary = Summary(schema=file.schema, skipped=len(in_blocks))
    references: list[tuple[int, str, Any, int]] = []
    try:
        for step, keyword, parameters in file:
            if step in in_blocks:
                continue
            known = project_targets if step == project else targets
            properties, found = encode_instance(keyword, parameters, schema, known)
            chain = schema.chain(keyword)
            class_name = chain[0] if chain else keyword.decode("ascii")
            references.extend((step, attribute, position, target) for attribute, position, target in found)
            summary.entities += 1
            summary.classes[class_name] = summary.classes.get(class_name, 0) + 1
            if step == project:
                summary.ifc_project_guid = properties.get("GlobalId")
            yield {
                "t": "n",
                "step": step,
                "labels": [name for name in chain if _IDENTIFIER.match(name)],
                "props": {"ifc_class": class_name, **properties, **heights.get(step, {})},
            }
        yield from _blocks(file, sorted(in_blocks))
    except StepError as exc:
        raise ConversionError(str(exc)) from exc
    inverses: dict[tuple[int, str, int], None] = {}
    for step, attribute, _position, target in references:
        for inverse in schema.refers_back(keywords[step], attribute, keywords[target]):
            inverses[(target, inverse, step)] = None
    summary.relationships = len(references) + len(inverses)
    yield {
        "t": "h",
        "schema": summary.schema,
        "entities": summary.entities,
        "relationships": summary.relationships,
        "skipped": summary.skipped,
        "ifc_project_guid": summary.ifc_project_guid,
        "classes": summary.classes,
        "lossless": True,
        "geometry": form,
        "ifc_header": encode_header(file.header_values),
    }
    for step, attribute, position, target in references:
        yield {"t": "e", "a": step, "b": target, "type": attribute, "position": position}
    for target, inverse, step in inverses:
        yield {"t": "e", "a": target, "b": step, "type": inverse}


def _second_parameter(file: StepFile, project: int | None) -> int | None:
    """The owner history of a project: its second attribute, if that is a reference at all."""
    if project is None:
        return None
    parameters = file[project][1]
    value = parameters[1] if len(parameters) > 1 else None
    return value if isinstance(value, int) else None


def _blocks(file: StepFile, steps: list[int]) -> Iterator[dict[str, Any]]:
    """The instances that are no nodes, as blocks of text in order of id."""
    lines: list[bytes] = []
    size = 0
    first = 0

    def block() -> dict[str, Any]:
        return {"t": "g", "first": first, "instances": len(lines), "text": encode_block(b"".join(lines))}

    for step in steps:
        keyword, parameters = file[step]
        line = b"#%d=%s;\n" % (step, statement_text(keyword, parameters))
        if not lines:
            first = step
        lines.append(line)
        size += len(line)
        if size >= BLOCK_BYTES:
            yield block()
            lines, size = [], 0
    if lines:
        yield block()


def write_jsonl(path: Path, out: Path, *, lossless: bool = False, **options: Any) -> dict[str, Any]:
    """Run a conversion and write the records to ``out``; returns the header record."""
    header: dict[str, Any] = {}
    records = convert_lossless(path, **options) if lossless else convert(path, **options)
    with out.open("w", encoding="utf-8") as handle:
        for record in records:
            if record["t"] == "h":
                header = record
            handle.write(json.dumps(record) + "\n")
    return header
