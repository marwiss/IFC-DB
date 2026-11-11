# This file was generated with the assistance of an AI coding tool.
"""Where the products are: a box per product in project coordinates, from one pass of IfcOpenShell's geometry.

The worker, ``python -m ifc_lpg.spatial IFC OUT [--memory-mb N] [--threads T] [--max-entities N]``, needs the extra
``ifcopenshell`` and runs in a process of its own. It runs the iterator once, in each shape's own coordinates, and writes
JSON Lines: a header (``h``); each mesh once (``m``), before the first product that uses it; each product (``p``) with
its storey, its mesh, the matrix from the mesh to project coordinates, column by column as glTF has it, and its box;
each product with a body whose shape could not be built (``x``); and an end (``end``) with the counts, without which the
file is cut short. Dataregium builds glTF from the same file.

:func:`import_spatial` sets ``bbox_min``, ``bbox_max`` and ``bbox_centre`` on the products' nodes as 3D points, and needs
no extra. The boxes are derived: the writer reads only the schema's attributes, so the file written from the graph stays
the same with or without them. They follow the world's axes, not the element's, and suit selection and nearness.
With ``meshes=True`` it also sets each product's mesh in project coordinates: ``mesh_points``, a list of 3D points, and
``mesh_faces``, three indices into them per triangle, so that Cypher can ask about every corner of a shape.
:func:`overlapping_boxes` lists the pairs of products whose boxes overlap, the candidates for a clash test, and
:func:`geometry_issues` the products whose shape could not be built, as issues of a validation report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from ifc_lpg.files import DEFAULT_LIMITS, Limits, _now, run_worker
from ifc_lpg.graph import Dialect, Graph, Neo4jGraph, is_whole, rebuild_point_indexes
from ifc_lpg.importer import BATCH

FORMAT = "ifc-lpg-geometry"
VERSION = 1
_BODY = {"body", "facetation"}
# How a product reaches the storey above it: (inverse attribute, the attribute of the relationship that points up).
_UP = (
    ("ContainedInStructure", "RelatingStructure"),
    ("Decomposes", "RelatingObject"),
    ("VoidsElements", "RelatingBuildingElement"),
    ("FillsVoids", "RelatingOpeningElement"),
    ("Nests", "RelatingObject"),
)


class SpatialError(ValueError):
    """The file cannot be read for its geometry, or the geometry does not fit the model."""


# The worker. IfcOpenShell is imported here only, inside the worker's process.


def _parent(instance: Any) -> Any:
    for inverse, attribute in _UP:
        try:
            relations = getattr(instance, inverse)
        except AttributeError:
            continue
        for relation in relations or ():
            parent = getattr(relation, attribute, None)
            if parent is not None:
                return parent
    return None


def storey_of(product: Any) -> str | None:
    """The GlobalId of the nearest storey above ``product``, or ``None`` outside a storey."""
    current, seen = product, set()
    while current is not None and current.id() not in seen:
        seen.add(current.id())
        if current.is_a("IfcBuildingStorey"):
            return current.GlobalId
        current = _parent(current)
    return None


def box_of(verts: tuple[float, ...], matrix: tuple[float, ...]) -> tuple[list[float], list[float]]:
    """The smallest and the largest corner of ``verts`` taken through ``matrix`` (16 numbers, column by column)."""
    low = [math.inf] * 3
    high = [-math.inf] * 3
    m = matrix
    for i in range(0, len(verts), 3):
        x, y, z = verts[i], verts[i + 1], verts[i + 2]
        point = (
            m[0] * x + m[4] * y + m[8] * z + m[12],
            m[1] * x + m[5] * y + m[9] * z + m[13],
            m[2] * x + m[6] * y + m[10] * z + m[14],
        )
        for axis in range(3):
            low[axis] = min(low[axis], point[axis])
            high[axis] = max(high[axis], point[axis])
    return low, high


def _material(material: Any) -> dict[str, Any]:
    try:
        diffuse: list[float] | None = [float(c) for c in material.diffuse.components]
    except Exception:
        diffuse = None
    transparency = float(material.transparency)
    return {
        "name": material.name,
        "diffuse": diffuse,
        "transparency": transparency if math.isfinite(transparency) else None,
    }


def _line(record: dict[str, Any]) -> str:
    return json.dumps(record, allow_nan=False) + "\n"


def write_geometry(source: Path, out: Path, *, threads: int = 1, max_entities: int = 5_000_000) -> dict[str, Any]:
    """Write the geometry records of ``source`` to ``out``. Returns the end record."""
    import ifcopenshell
    import ifcopenshell.geom

    from ifc_lpg.step import StepError, StepFile

    started = time.monotonic()
    data = source.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    try:
        instances = len(StepFile(data))  # refuses a file cut short before IfcOpenShell reads it
    except StepError as exc:
        raise SpatialError(str(exc)) from exc
    del data
    if instances > max_entities:
        raise SpatialError(f"the model has more than {max_entities} entities")
    model = ifcopenshell.open(str(source))
    read = sum(1 for _ in model)
    if read != instances:
        raise SpatialError(f"IfcOpenShell read {read} of the file's {instances} instances")

    meshes: dict[str, str | None] = {}  # id: why its products cannot use it, or None
    built: set[int] = set()
    products = failed = 0
    with out.open("w", encoding="utf-8") as handle:

        def fail(product: Any, error: str) -> None:
            nonlocal failed
            failed += 1
            handle.write(
                _line(
                    {
                        "t": "x",
                        "step": product.id(),
                        "global_id": product.GlobalId,
                        "class": product.is_a(),
                        "error": error,
                    }
                )
            )

        header = {
            "t": "h",
            "format": FORMAT,
            "version": VERSION,
            "schema": model.schema_identifier,
            "ifcopenshell": ifcopenshell.version,
            "units": "m",
            "coordinates": "project",
            "source": {"sha256": digest},
        }
        handle.write(_line(header))
        settings = ifcopenshell.geom.settings()
        iterator = ifcopenshell.geom.iterator(settings, model, max(1, threads))
        if iterator.initialize():
            while True:
                shape: Any = iterator.get()  # a triangulated element, since the settings ask for no B-rep
                product = model.by_id(shape.id)
                built.add(shape.id)
                geometry = shape.geometry
                if geometry.id not in meshes:
                    try:
                        mesh = {
                            "t": "m",
                            "id": geometry.id,
                            "verts": list(geometry.verts),
                            "faces": list(geometry.faces),
                            "material_ids": list(geometry.material_ids),
                            "materials": [_material(material) for material in geometry.materials],
                        }
                        if geometry.normals:
                            mesh["normals"] = list(geometry.normals)
                        meshes[geometry.id] = None if geometry.verts else "the shape is empty"
                        if geometry.verts:
                            handle.write(_line(mesh))
                    except ValueError:
                        meshes[geometry.id] = "the shape has a coordinate that is not a number"
                why = meshes[geometry.id]
                if why is not None:
                    fail(product, why)
                else:
                    matrix = tuple(float(value) for value in shape.transformation.matrix)
                    low, high = box_of(geometry.verts, matrix)
                    try:
                        line = _line(
                            {
                                "t": "p",
                                "step": shape.id,
                                "global_id": product.GlobalId,
                                "class": product.is_a(),
                                "storey": storey_of(product),
                                "mesh": geometry.id,
                                "matrix": list(matrix),
                                "bbox_min": low,
                                "bbox_max": high,
                                "bbox_centre": [(a + b) / 2 for a, b in zip(low, high, strict=True)],
                            }
                        )
                    except ValueError:
                        fail(product, "the box has a coordinate that is not a number")
                    else:
                        handle.write(line)
                        products += 1
                if not iterator.next():
                    break
        for product in model.by_type("IfcProduct"):
            shape = product.Representation
            if product.id() in built or shape is None:
                continue
            if any((r.RepresentationIdentifier or "").lower() in _BODY for r in shape.Representations or ()):
                fail(product, "IfcOpenShell built no shape")
        end = {
            "t": "end",
            "products": products,
            "meshes": sum(1 for why in meshes.values() if why is None),
            "failed": failed,
            "seconds": round(time.monotonic() - started, 3),
        }
        handle.write(_line(end))
    return end


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ifc-lpg-spatial")
    parser.add_argument("ifc", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--memory-mb", type=int, default=0)
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--max-entities", type=int, default=5_000_000)
    args = parser.parse_args(argv)

    from ifc_lpg.worker import keep_stdout_for_the_answer, limit_memory

    if args.memory_mb:
        limit_memory(args.memory_mb)
    keep_stdout_for_the_answer()
    try:
        end = write_geometry(args.ifc, args.out, threads=args.threads, max_entities=args.max_entities)
    except SpatialError as exc:
        sys.stderr.write(json.dumps({"error": str(exc)[:500]}) + "\n")
        return 3
    except Exception as exc:
        sys.stderr.write(json.dumps({"error": f"{type(exc).__name__}: {str(exc)[:500]}"}) + "\n")
        return 4
    sys.stdout.write(json.dumps(end) + "\n")
    sys.stdout.flush()
    return 0


# The graph side, without extras.


def read_records(records_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """The header, the products and the end of a geometry file. Refuses one that is not whole."""
    header, products, _failures, end, _meshes = _read(records_path)
    return header, products, end


def geometry_issues(records_path: Path) -> list[dict[str, Any]]:
    """The products of a geometry file whose shape IfcOpenShell could not build, as issues in the form of a validation
    report (``kind`` ``geometry``), with the GlobalId of each. The schema and buildingSMART's rules let such a shape
    through, such as a profile 0 m wide."""
    return _read(records_path)[2]


def _read(records_path: Path, *, with_meshes: bool = False) -> tuple[Any, ...]:
    """The header, the products, the failures, the end and, if asked for, the meshes by id."""
    header: dict[str, Any] | None = None
    end: dict[str, Any] | None = None
    products: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    found: dict[str, tuple[list[float], list[int]]] = {}
    meshes = 0
    with records_path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if end is not None:
                raise SpatialError(f"line {number} comes after the end")
            if line.startswith('{"t": "m"'):
                meshes += 1  # a mesh is for glTF, and for the graph only if asked for
                if with_meshes:
                    found.update([_mesh(line, number)])
                continue
            try:
                record = json.loads(line, parse_constant=_no_constant)
            except ValueError as exc:
                raise SpatialError(f"line {number} is no JSON") from exc
            kind = record.get("t") if isinstance(record, dict) else None
            if number == 1:
                if kind != "h" or record.get("format") != FORMAT or record.get("version") != VERSION:
                    raise SpatialError(f"the file is not {FORMAT} version {VERSION}")
                header = record
            elif kind == "p":
                products.append(_product(record, number, with_meshes))
            elif kind == "x":
                failures.append(_failure(record, number))
            elif kind == "end":
                end = record
            else:
                raise SpatialError(f"line {number} is of no known kind")
    if header is None or end is None:
        raise SpatialError("the file is cut short: it has no end")
    if (end.get("products"), end.get("meshes"), end.get("failed")) != (len(products), meshes, len(failures)):
        raise SpatialError("the file's end does not count what the file holds")
    if with_meshes and any(product["mesh"] not in found for product in products):
        raise SpatialError("a product's mesh is not in the file")
    return header, products, failures, end, found


def _mesh(line: str, number: int) -> tuple[str, tuple[list[float], list[int]]]:
    try:
        record = json.loads(line, parse_constant=_no_constant)
    except ValueError as exc:
        raise SpatialError(f"line {number} is no JSON") from exc
    mesh, verts, faces = record.get("id"), record.get("verts"), record.get("faces")
    if not (
        isinstance(mesh, str)
        and isinstance(verts, list)
        and len(verts) % 3 == 0
        and all(isinstance(v, int | float) and not isinstance(v, bool) for v in verts)
        and isinstance(faces, list)
        and len(faces) % 3 == 0
        and all(isinstance(f, int) and not isinstance(f, bool) and 0 <= f < len(verts) // 3 for f in faces)
    ):
        raise SpatialError(f"line {number} is no mesh of corners and triangles")
    return mesh, ([float(v) for v in verts], faces)


def world_points(verts: list[float], matrix: list[float]) -> list[list[float]]:
    """The corners ``verts`` (flat, three numbers each) through ``matrix`` (16 numbers, column by column)."""
    m = matrix
    return [
        [
            m[0] * x + m[4] * y + m[8] * z + m[12],
            m[1] * x + m[5] * y + m[9] * z + m[13],
            m[2] * x + m[6] * y + m[10] * z + m[14],
        ]
        for x, y, z in zip(verts[0::3], verts[1::3], verts[2::3], strict=True)
    ]


def _failure(record: dict[str, Any], number: int) -> dict[str, Any]:
    fields = (record.get("step"), record.get("global_id"), record.get("class"), record.get("error"))
    if not isinstance(fields[0], int) or not all(isinstance(value, str) for value in fields[1:]):
        raise SpatialError(f"line {number} names no product and why its shape failed")
    step, global_id, ifc_class, error = fields
    return {
        "kind": "geometry",
        "severity": "error",
        "message": f"IfcOpenShell could not build the shape of this {ifc_class}: {error}"[:1000],
        "step_id": step,
        "global_id": global_id,
        "attribute": "Representation",
    }


def _no_constant(name: str) -> Any:
    raise ValueError(f"{name} is not a number")


def _product(record: dict[str, Any], number: int, with_meshes: bool = False) -> dict[str, Any]:
    row: dict[str, Any] = {"step": record.get("step"), "global_id": record.get("global_id")}
    if not isinstance(row["step"], int) or not isinstance(row["global_id"], str):
        raise SpatialError(f"line {number} names no product")
    if with_meshes:
        mesh, matrix = record.get("mesh"), record.get("matrix")
        if not (isinstance(mesh, str) and isinstance(matrix, list) and len(matrix) == 16):
            raise SpatialError(f"line {number} has no mesh and matrix")
        if not all(isinstance(v, int | float) and not isinstance(v, bool) for v in matrix):
            raise SpatialError(f"line {number} has a matrix of something else than numbers")
        row["mesh"], row["matrix"] = mesh, [float(v) for v in matrix]
    for key in ("bbox_min", "bbox_max", "bbox_centre"):
        value = record.get(key)
        if not (isinstance(value, list) and len(value) == 3 and all(isinstance(v, int | float) for v in value)):
            raise SpatialError(f"line {number} has no {key} of three numbers")
        row[key] = [float(v) for v in value]
    return row


_SET = ", ".join(
    f"p.{key} = point({{x: row.{key}[0], y: row.{key}[1], z: row.{key}[2]}})"
    for key in ("bbox_min", "bbox_max", "bbox_centre")
)


MESH_NUMBERS = 300_000  # the corners and indices of the meshes set in one transaction


async def import_spatial(
    graph: Graph, model_id: str, records_path: Path, *, dialect: Dialect | None = None, meshes: bool = False
) -> dict[str, Any]:
    """Set the boxes of ``records_path`` on the products of the model ``model_id``, in place of any from before.

    Every product must be a node of the model with the same GlobalId, or nothing is changed. Returns the counts.
    With ``meshes``, each product also gets its mesh in project coordinates, in metres: ``mesh_points`` and
    ``mesh_faces``; they are long lists, and off by default. With ``dialect`` given, the point index is made anew
    afterwards where the database does not keep it up to date (Memgraph 3.14); that needs a :class:`Neo4jGraph`, for
    its ``ddl``."""
    header, products, _failures, end, mesh_map = _read(records_path, with_meshes=meshes)
    rows = await graph.run(
        "MATCH (m:Model {id: $id}) RETURN m.state AS state, m.imported_at AS imported_at, m.format AS format",
        id=model_id,
    )
    if not rows:
        raise SpatialError("there is no such model")
    if (rows[0]["format"] or "step") != "step" or not is_whole(rows[0]):
        raise SpatialError("the model is not a STEP model whose import is done")
    for product in products:
        product["id"] = f"{model_id}:{product['step']}"
    found = 0
    for start in range(0, len(products), BATCH):
        answer = await graph.run(
            "UNWIND $rows AS row MATCH (p:Entity {id: row.id}) WHERE p.GlobalId = row.global_id RETURN count(p) AS n",
            rows=[{"id": p["id"], "global_id": p["global_id"]} for p in products[start : start + BATCH]],
        )
        found += int(answer[0]["n"]) if answer else 0
    if found != len(products):
        raise SpatialError(f"{len(products) - found} of the {len(products)} products are not in the model")
    while True:
        answer = await graph.run(
            "MATCH (p:Entity {model_id: $id}) WHERE p.bbox_centre IS NOT NULL OR p.mesh_faces IS NOT NULL "
            "WITH p LIMIT $batch REMOVE p.bbox_min, p.bbox_max, p.bbox_centre, p.mesh_points, p.mesh_faces "
            "RETURN count(*) AS n",
            id=model_id,
            batch=BATCH,
        )
        if not answer or int(answer[0]["n"]) < BATCH:
            break
    for start in range(0, len(products), BATCH):
        await graph.run(
            f"UNWIND $rows AS row MATCH (p:Entity {{id: row.id}}) SET {_SET}",
            rows=[
                {key: p[key] for key in ("id", "bbox_min", "bbox_max", "bbox_centre")}
                for p in products[start : start + BATCH]
            ],
        )
    if meshes:
        await _set_meshes(graph, products, mesh_map)
    fields = {
        "spatial_at": _now(),
        "spatial_ifcopenshell": header.get("ifcopenshell"),
        "spatial_products": len(products),
        "spatial_failed": end["failed"],
        "spatial_meshes": meshes,
    }
    await graph.run("MATCH (m:Model {id: $id}) SET m += $fields", id=model_id, fields=fields)
    if dialect is not None:
        if not isinstance(graph, Neo4jGraph):
            raise SpatialError("making the point index anew needs a Neo4jGraph")
        await rebuild_point_indexes(graph, dialect)
    return {"products": len(products), "meshes": end["meshes"], "failed": end["failed"]}


async def _set_meshes(
    graph: Graph, products: list[dict[str, Any]], found: dict[str, tuple[list[float], list[int]]]
) -> None:
    rows: list[dict[str, Any]] = []
    numbers = 0
    for product in products:
        verts, faces = found[product["mesh"]]
        rows.append({"id": product["id"], "points": world_points(verts, product["matrix"]), "faces": faces})
        numbers += len(verts) + len(faces)
        if numbers >= MESH_NUMBERS or len(rows) >= BATCH or product is products[-1]:
            await graph.run(
                "UNWIND $rows AS row MATCH (p:Entity {id: row.id}) "
                "SET p.mesh_points = [v IN row.points | point({x: v[0], y: v[1], z: v[2]})], p.mesh_faces = row.faces",
                rows=rows,
            )
            rows, numbers = [], 0


_OVERLAPPING = (
    "MATCH (a:Entity {model_id: $model}) WHERE a.bbox_centre IS NOT NULL AND NOT a.ifc_class IN $exclude "
    "MATCH (b:Entity) WHERE point.withinBBox(b.bbox_centre, "
    "point({x: a.bbox_min.x - $x, y: a.bbox_min.y - $y, z: a.bbox_min.z - $z}), "
    "point({x: a.bbox_max.x + $x, y: a.bbox_max.y + $y, z: a.bbox_max.z + $z})) "
    "AND b.model_id = $other AND NOT b.ifc_class IN $exclude AND ($other <> $model OR a.step_id < b.step_id) "
    "AND a.bbox_min.x <= b.bbox_max.x + $c AND b.bbox_min.x <= a.bbox_max.x + $c "
    "AND a.bbox_min.y <= b.bbox_max.y + $c AND b.bbox_min.y <= a.bbox_max.y + $c "
    "AND a.bbox_min.z <= b.bbox_max.z + $c AND b.bbox_min.z <= a.bbox_max.z + $c "
    "RETURN a.step_id AS step, a.GlobalId AS global_id, a.ifc_class AS ifc_class, b.step_id AS other_step, "
    "b.GlobalId AS other_global_id, b.ifc_class AS other_ifc_class ORDER BY step, other_step"
)


async def overlapping_boxes(
    graph: Graph,
    model_id: str,
    other_model_id: str | None = None,
    *,
    clearance: float = 0.0,
    exclude: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    """The pairs of products whose boxes overlap or come within ``clearance`` metres of each other: in one model, or
    between ``model_id`` and ``other_model_id``, whose boxes must then be in the same coordinates. A negative clearance
    asks for that much overlap, which leaves out boxes that only touch. Products of the classes in ``exclude`` are left
    out. The pairs are candidates for a clash test on the shapes, which the boxes cannot settle.

    The point index on ``bbox_centre`` finds each product's candidates: a box that overlaps another has its centre
    within the other's box widened by half the largest box of the model."""
    other = other_model_id or model_id
    rows = await graph.run(
        "MATCH (b:Entity {model_id: $other}) WHERE b.bbox_centre IS NOT NULL AND NOT b.ifc_class IN $exclude "
        "RETURN max(b.bbox_max.x - b.bbox_min.x) AS x, max(b.bbox_max.y - b.bbox_min.y) AS y, "
        "max(b.bbox_max.z - b.bbox_min.z) AS z",
        other=other,
        exclude=list(exclude),
    )
    if not rows or rows[0]["x"] is None:
        return []
    reach = {axis: rows[0][axis] / 2 + clearance + 1e-6 for axis in "xyz"}
    return await graph.run(
        _OVERLAPPING, model=model_id, other=other, exclude=list(exclude), c=float(clearance), **reach
    )


async def spatial_file(
    source: Path, target: Path, *, threads: int = 2, limits: Limits = DEFAULT_LIMITS
) -> dict[str, Any]:
    """Write the geometry records of ``source`` to ``target`` in the worker. Returns the end record."""
    command = [sys.executable, "-m", "ifc_lpg.spatial", str(source), str(target)]
    command += [
        "--memory-mb",
        str(limits.memory_mb),
        "--threads",
        str(threads),
        "--max-entities",
        str(limits.max_entities),
    ]
    answer = await run_worker(command, "the geometry", limits.timeout_seconds)
    return json.loads(answer.strip().splitlines()[-1])


async def add_spatial(
    graph: Graph,
    source: Path,
    model_id: str,
    *,
    dialect: Dialect | None = None,
    meshes: bool = False,
    threads: int = 2,
    limits: Limits = DEFAULT_LIMITS,
) -> dict[str, Any]:
    """Work out the boxes of ``source`` and set them on the model ``model_id``, which was imported from it; with
    ``meshes``, the meshes as well."""
    with tempfile.TemporaryDirectory(prefix="ifc-lpg-") as tmp:
        records = Path(tmp) / "geometry.jsonl"
        await spatial_file(source, records, threads=threads, limits=limits)
        return await import_spatial(graph, model_id, records, dialect=dialect, meshes=meshes)


if __name__ == "__main__":
    sys.exit(main())
