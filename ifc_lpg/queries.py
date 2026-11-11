# This file was generated with the assistance of an AI coding tool.
"""Named queries on a model in the graph: what a reader asks of a model, by name and with parameters.

How many elements of each class a model has, which storeys and spaces, how large they are, what the walls are made of,
which value a property has. A caller asks by name and gets rows back, and never writes Cypher of its own, so nothing
outside this package needs to know the graph's form: that a class and its supertypes are labels, that a typed value has
its keyword in ``NominalValue__type``, which way ``RELATED_ELEMENTS`` and ``RELATING_STRUCTURE`` point, and the four
forms a material comes in. :data:`QUERIES` is the catalogue, :func:`catalogue` the same as plain data for a listing,
:func:`run_query` runs one, and :func:`property_values` looks one property up for a list of GlobalIds.

Every statement names its model by id, takes values only as parameters and only reads. The queries use the forward
relationships, which every form of the graph has.
"""

from __future__ import annotations

import asyncio
import base64
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from ifc_lpg.graph import WHOLE, Graph

MAX_ROWS = 10_000  # at most this many rows in an answer, unless the caller asks for fewer
TIMEOUT_SECONDS = 10.0  # at most this long for a query to run, unless the caller says otherwise
MAX_GLOBAL_IDS = 1_000  # at most this many GlobalIds in one lookup

_MODEL = f"""
MATCH (m:Model {{id: $model_id}})
WHERE {WHOLE}
"""
_CLASS = re.compile(r"^Ifc[A-Za-z0-9]{1,64}$")
_GUID = re.compile(r"^[0-9A-Za-z_$]{22}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
KINDS = ("text", "class", "global_id")


class InvalidParameters(ValueError):
    """The parameters do not fit the query; the message says which and why."""


class UnknownQuery(LookupError):
    """There is no query by that name."""


@dataclass(frozen=True)
class Parameter:
    """One parameter of a query. ``kind`` says what it must look like: ``text`` is 1 to 255 characters with no control
    characters, ``class`` the name of an IFC class, and ``global_id`` 22 characters of IFC's base 64."""

    name: str
    description: str
    required: bool = False
    kind: str = "text"

    def value_of(self, raw: str) -> str:
        if not isinstance(raw, str):
            raise InvalidParameters(f"{self.name} must be text")
        if self.kind == "class":
            if not _CLASS.match(raw):
                raise InvalidParameters(f"{self.name} must be the name of an IFC class, such as IfcWall")
        elif self.kind == "global_id":
            if not _GUID.match(raw):
                raise InvalidParameters(f"{self.name} must be a GlobalId: 22 characters of IFC's base 64")
        elif not 1 <= len(raw) <= 255 or _CONTROL.search(raw):
            raise InvalidParameters(f"{self.name} must be 1 to 255 characters, with no control characters")
        return raw


@dataclass(frozen=True)
class NamedQuery:
    """A query by name: what it answers, the columns of its rows in order, and the parameters it takes."""

    name: str
    description: str
    columns: tuple[str, ...]
    body: str
    parameters: tuple[Parameter, ...] = field(default=())

    @property
    def cypher(self) -> str:
        """The statement: the model by its id, the query, and a limit on the rows."""
        return _MODEL + self.body + "LIMIT $row_limit\n"

    def bind(self, raw: Mapping[str, str | None]) -> dict[str, str | None]:
        """The query's parameters from what a caller sent, each checked. One that is left out, None or empty is None.
        Raises :class:`InvalidParameters`."""
        known = {parameter.name for parameter in self.parameters}
        unknown = sorted(set(raw) - known)
        if unknown:
            raise InvalidParameters(f"{self.name} takes no parameter {', '.join(unknown)}")
        values: dict[str, str | None] = {}
        for parameter in self.parameters:
            given = raw.get(parameter.name)
            if given is None or given == "":
                if parameter.required:
                    raise InvalidParameters(f"{self.name} needs the parameter {parameter.name}")
                values[parameter.name] = None
            else:
                values[parameter.name] = parameter.value_of(given)
        return values


_OF_CLASS = Parameter("class", "Only elements of this IFC class or its subclasses, such as IfcWall", kind="class")

QUERIES: dict[str, NamedQuery] = {
    query.name: query
    for query in (
        NamedQuery(
            "entity",
            "One rooted entity by its GlobalId: its class, every literal attribute, and the instances it refers to and "
            "is referred to by, each by the IFC attribute, STEP id and class, with the GlobalId where it has one.",
            ("global_id", "class", "step_id", "attributes", "references", "referenced_by"),
            """
            MATCH (e:IfcRoot {model_id: m.id})
            WHERE e.GlobalId = $global_id
            OPTIONAL MATCH (e)-[r]->(t:Entity)
            WITH e, collect(CASE WHEN r IS NULL THEN NULL ELSE
                 {attribute: type(r), step_id: t.step_id, global_id: t.GlobalId, class: t.ifc_class} END) AS references
            OPTIONAL MATCH (e)<-[r2]-(s:Entity)
            WITH e, references, collect(CASE WHEN r2 IS NULL THEN NULL ELSE
                 {attribute: type(r2), step_id: s.step_id, global_id: s.GlobalId, class: s.ifc_class} END) AS referenced_by
            RETURN e.GlobalId AS global_id, e.ifc_class AS class, e.step_id AS step_id, properties(e) AS attributes,
                   references, referenced_by
            """,
            (Parameter("global_id", "The GlobalId of a rooted entity, such as a wall or a storey", True, "global_id"),),
        ),
        NamedQuery(
            "element-classes",
            "How many elements the model has of each class.",
            ("class", "count"),
            """
            MATCH (e:IfcElement {model_id: m.id})
            RETURN e.ifc_class AS class, count(e) AS count
            ORDER BY count DESC, class
            """,
        ),
        NamedQuery(
            "elements",
            "The elements of a class, with the spatial structure that contains each.",
            ("global_id", "class", "name", "container"),
            """
            MATCH (e:IfcRoot {model_id: m.id})
            WHERE $class IN labels(e)
            OPTIONAL MATCH (e)<-[:RELATED_ELEMENTS]-(:IfcRelContainedInSpatialStructure)-[:RELATING_STRUCTURE]->(s)
            RETURN e.GlobalId AS global_id, e.ifc_class AS class, e.Name AS name, s.Name AS container
            ORDER BY name, global_id
            """,
            (Parameter("class", "The IFC class, such as IfcWall; its subclasses count too", True, "class"),),
        ),
        NamedQuery(
            "storeys",
            "The building storeys, with their elevation and how many elements and spaces each holds.",
            ("global_id", "name", "elevation", "elements", "spaces"),
            """
            MATCH (s:IfcBuildingStorey {model_id: m.id})
            OPTIONAL MATCH (s)<-[:RELATING_STRUCTURE]-(:IfcRelContainedInSpatialStructure)-[:RELATED_ELEMENTS]->(e)
            WITH s, count(DISTINCT e) AS elements
            OPTIONAL MATCH (s)<-[:RELATING_OBJECT]-(:IfcRelAggregates)-[:RELATED_OBJECTS]->(sp:IfcSpace)
            RETURN s.GlobalId AS global_id, s.Name AS name, s.Elevation AS elevation, elements,
                   count(DISTINCT sp) AS spaces
            ORDER BY elevation, name
            """,
        ),
        NamedQuery(
            "spaces",
            "The spaces, with their storey and their net and gross floor areas where the model gives them.",
            ("global_id", "name", "long_name", "storey", "net_floor_area", "gross_floor_area"),
            """
            MATCH (sp:IfcSpace {model_id: m.id})
            OPTIONAL MATCH (sp)<-[:RELATED_OBJECTS]-(:IfcRelAggregates)-[:RELATING_OBJECT]->(s:IfcBuildingStorey)
            OPTIONAL MATCH (sp)<-[:RELATED_OBJECTS]-(:IfcRelDefinesByProperties)-[:RELATING_PROPERTY_DEFINITION]
                  ->(:IfcElementQuantity)-[:QUANTITIES]->(q:IfcQuantityArea)
            WHERE q.Name IN ['NetFloorArea', 'GrossFloorArea']
            WITH sp, s, collect(q) AS areas
            RETURN sp.GlobalId AS global_id, sp.Name AS name, sp.LongName AS long_name, s.Name AS storey,
                   head([a IN areas WHERE a.Name = 'NetFloorArea' | a.AreaValue]) AS net_floor_area,
                   head([a IN areas WHERE a.Name = 'GrossFloorArea' | a.AreaValue]) AS gross_floor_area
            ORDER BY storey, name, global_id
            """,
        ),
        NamedQuery(
            "areas",
            "The net floor area of each storey's spaces, added up; a building's area is the sum of its storeys'.",
            ("storey", "elevation", "spaces", "net_floor_area"),
            """
            MATCH (s:IfcBuildingStorey {model_id: m.id})
            OPTIONAL MATCH (s)<-[:RELATING_OBJECT]-(:IfcRelAggregates)-[:RELATED_OBJECTS]->(sp:IfcSpace)
            OPTIONAL MATCH (sp)<-[:RELATED_OBJECTS]-(:IfcRelDefinesByProperties)-[:RELATING_PROPERTY_DEFINITION]
                  ->(:IfcElementQuantity)-[:QUANTITIES]->(q:IfcQuantityArea {Name: 'NetFloorArea'})
            RETURN s.Name AS storey, s.Elevation AS elevation, count(DISTINCT sp) AS spaces,
                   sum(q.AreaValue) AS net_floor_area
            ORDER BY elevation, storey
            """,
        ),
        NamedQuery(
            "materials",
            "The materials of each element: a material of its own, or those of its layers, list or constituents.",
            ("global_id", "class", "name", "materials"),
            """
            MATCH (e:IfcElement {model_id: m.id})
            WHERE $class IS NULL OR $class IN labels(e)
            OPTIONAL MATCH (e)<-[:RELATED_OBJECTS]-(:IfcRelAssociatesMaterial)-[:RELATING_MATERIAL]->(r)
            OPTIONAL MATCH (r)-[:FOR_LAYER_SET]->(:IfcMaterialLayerSet)-[:MATERIAL_LAYERS]->(:IfcMaterialLayer)
                  -[:MATERIAL]->(usage:IfcMaterial)
            OPTIONAL MATCH (r)-[:MATERIAL_LAYERS]->(:IfcMaterialLayer)-[:MATERIAL]->(layer:IfcMaterial)
            OPTIONAL MATCH (r)-[:MATERIALS]->(listed:IfcMaterial)
            OPTIONAL MATCH (r)-[:MATERIAL_CONSTITUENTS]->(:IfcMaterialConstituent)-[:MATERIAL]->(part:IfcMaterial)
            WITH e,
                 collect(DISTINCT CASE WHEN 'IfcMaterial' IN labels(r) THEN r.Name END)
                 + collect(DISTINCT usage.Name) + collect(DISTINCT layer.Name)
                 + collect(DISTINCT listed.Name) + collect(DISTINCT part.Name) AS materials
            RETURN e.GlobalId AS global_id, e.ifc_class AS class, e.Name AS name, materials
            ORDER BY name, global_id
            """,
            (_OF_CLASS,),
        ),
        NamedQuery(
            "property",
            "The value of one property of a property set on every object that has it, with the value's IFC type.",
            ("global_id", "class", "name", "value", "type"),
            """
            MATCH (e:IfcObject {model_id: m.id})
            WHERE $class IS NULL OR $class IN labels(e)
            MATCH (e)<-[:RELATED_OBJECTS]-(:IfcRelDefinesByProperties)-[:RELATING_PROPERTY_DEFINITION]
                  ->(:IfcPropertySet {Name: $pset})-[:HAS_PROPERTIES]->(v:IfcPropertySingleValue {Name: $property})
            RETURN e.GlobalId AS global_id, e.ifc_class AS class, e.Name AS name, v.NominalValue AS value,
                   v.NominalValue__type AS type
            ORDER BY name, global_id
            """,
            (
                Parameter("pset", "The property set, such as Pset_WallCommon", True),
                Parameter("property", "The property, such as FireRating", True),
                _OF_CLASS,
            ),
        ),
    )
}

_PROPERTY_VALUES = (
    _MODEL
    + """
UNWIND $global_ids AS wanted
MATCH (e:Entity {model_id: m.id, GlobalId: wanted})
OPTIONAL MATCH (e)<-[:RELATED_OBJECTS]-(:IfcRelDefinesByProperties)-[:RELATING_PROPERTY_DEFINITION]
      ->(:IfcPropertySet {Name: $pset})-[:HAS_PROPERTIES]->(v:IfcPropertySingleValue {Name: $property})
RETURN e.GlobalId AS global_id, v.NominalValue AS value, v.NominalValue__type AS type
ORDER BY global_id
"""
)


def catalogue() -> list[dict[str, Any]]:
    """The queries as plain data, for a listing: name, description, parameters and columns."""
    return [
        {
            "name": query.name,
            "description": query.description,
            "parameters": [
                {
                    "name": parameter.name,
                    "description": parameter.description,
                    "required": parameter.required,
                    "kind": parameter.kind,
                }
                for parameter in query.parameters
            ],
            "columns": list(query.columns),
        }
        for query in QUERIES.values()
    ]


def json_value(value: Any) -> Any:
    """A value of the graph as JSON has it: a point is its ``srid`` and coordinates by name, a date or time its ISO
    text, bytes base 64, and a number that is not finite null."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if hasattr(value, "iso_format"):  # before the sequences: a duration is a tuple
        return value.iso_format()
    if isinstance(value, tuple) and hasattr(value, "srid"):  # so is a point
        return {"srid": value.srid, **dict(zip(("x", "y", "z"), value, strict=False))}
    if isinstance(value, Mapping):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [json_value(item) for item in value]
    if isinstance(value, bytes | bytearray):
        return base64.b64encode(bytes(value)).decode("ascii")
    return str(value)


async def _rows(graph: Graph, statement: str, params: dict[str, Any], timeout_seconds: float) -> list[dict[str, Any]]:
    """The rows of a reading statement, within the time. A graph that can do better than the caller's own clock, such
    as telling the server to stop the statement, offers ``read(statement, params, timeout_seconds=...)``; it is used
    when it is there."""
    read = getattr(graph, "read", None)
    if read is not None:
        return await read(statement, params, timeout_seconds=timeout_seconds)
    return await asyncio.wait_for(graph.run(statement, **params), timeout_seconds)


async def run_query(
    graph: Graph,
    model_id: str,
    name: str,
    parameters: Mapping[str, str | None] | None = None,
    *,
    max_rows: int = MAX_ROWS,
    timeout_seconds: float = TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Ask a model one of the named queries. Returns ``{"name", "columns", "rows", "truncated"}``: the rows are lists in
    the order of the columns, of JSON values, and ``truncated`` says that there were more than ``max_rows`` of them.

    A model that is not there, or not done, has no rows. Raises :class:`UnknownQuery` for a name that is no query,
    :class:`InvalidParameters` for parameters that do not fit, and ``TimeoutError`` when the time is up."""
    query = QUERIES.get(name)
    if query is None:
        raise UnknownQuery(f"there is no query named {str(name)[:60]!r}")
    if max_rows < 1:
        raise InvalidParameters("max_rows must be at least 1")
    values = query.bind(parameters or {})
    params = {**values, "model_id": model_id, "row_limit": max_rows + 1}
    found = await _rows(graph, query.cypher, params, timeout_seconds)
    rows = [[json_value(row.get(column)) for column in query.columns] for row in found[:max_rows]]
    return {"name": query.name, "columns": list(query.columns), "rows": rows, "truncated": len(found) > max_rows}


async def property_values(
    graph: Graph,
    model_id: str,
    *,
    pset: str,
    property: str,
    global_ids: Iterable[str],
    timeout_seconds: float = TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    """The value of one property of a property set for the instances with these GlobalIds: a row ``{"global_id",
    "value", "type"}`` for each that the model has, with ``value`` and ``type`` None where it lacks the property, in
    order of GlobalId. At most :data:`MAX_GLOBAL_IDS` in one call. Raises :class:`InvalidParameters`."""
    text = Parameter("pset", ""), Parameter("property", "")
    wanted = sorted({Parameter("global_ids", "", kind="global_id").value_of(global_id) for global_id in global_ids})
    if len(wanted) > MAX_GLOBAL_IDS:
        raise InvalidParameters(f"at most {MAX_GLOBAL_IDS} GlobalIds in one call")
    params = {
        "model_id": model_id,
        "pset": text[0].value_of(pset),
        "property": text[1].value_of(property),
        "global_ids": wanted,
    }
    if not wanted:
        return []
    found = await _rows(graph, _PROPERTY_VALUES, params, timeout_seconds)
    return [{key: json_value(row.get(key)) for key in ("global_id", "value", "type")} for row in found]
