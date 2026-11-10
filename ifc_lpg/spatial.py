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
from ifc_lpg.graph import Dialect, Graph, Neo4jGraph, rebuild_point_indexes
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
    header: dict[str, Any] | None = None
    end: dict[str, Any] | None = None
    products: list[dict[str, Any]] = []
    meshes = failed = 0
    with records_path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if end is not None:
                raise SpatialError(f"line {number} comes after the end")
            if line.startswith('{"t": "m"'):
                meshes += 1  # a mesh is for glTF, not for the graph
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
                products.append(_product(record, number))
            elif kind == "x":
                failed += 1
            elif kind == "end":
                end = record
            else:
                raise SpatialError(f"line {number} is of no known kind")
    if header is None or end is None:
        raise SpatialError("the file is cut short: it has no end")
    if (end.get("products"), end.get("meshes"), end.get("failed")) != (len(products), meshes, failed):
        raise SpatialError("the file's end does not count what the file holds")
    return header, products, end


def _no_constant(name: str) -> Any:
    raise ValueError(f"{name} is not a number")


def _product(record: dict[str, Any], number: int) -> dict[str, Any]:
    row: dict[str, Any] = {"step": record.get("step"), "global_id": record.get("global_id")}
    if not isinstance(row["step"], int) or not isinstance(row["global_id"], str):
        raise SpatialError(f"line {number} names no product")
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


async def import_spatial(
    graph: Graph, model_id: str, records_path: Path, *, dialect: Dialect | None = None
) -> dict[str, Any]:
    """Set the boxes of ``records_path`` on the products of the model ``model_id``, in place of any from before.

    Every product must be a node of the model with the same GlobalId, or nothing is changed. Returns the counts.
    With ``dialect`` given, the point index is made anew afterwards where the database does not keep it up to date
    (Memgraph 3.14); that needs a :class:`Neo4jGraph`, for its ``ddl``."""
    header, products, end = read_records(records_path)
    rows = await graph.run("MATCH (m:Model {id: $id}) RETURN m.state AS state, m.format AS format", id=model_id)
    if not rows:
        raise SpatialError("there is no such model")
    if (rows[0]["format"] or "step") != "step" or rows[0]["state"] != "done":
        raise SpatialError(f"the model is {rows[0]['format']} and {rows[0]['state']}, not a STEP model that is done")
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
            "MATCH (p:Entity {model_id: $id}) WHERE p.bbox_centre IS NOT NULL WITH p LIMIT $batch "
            "REMOVE p.bbox_min, p.bbox_max, p.bbox_centre RETURN count(*) AS n",
            id=model_id,
            batch=BATCH,
        )
        if not answer or int(answer[0]["n"]) < BATCH:
            break
    for start in range(0, len(products), BATCH):
        await graph.run(
            f"UNWIND $rows AS row MATCH (p:Entity {{id: row.id}}) SET {_SET}", rows=products[start : start + BATCH]
        )
    fields = {
        "spatial_at": _now(),
        "spatial_ifcopenshell": header.get("ifcopenshell"),
        "spatial_products": len(products),
        "spatial_failed": end["failed"],
    }
    await graph.run("MATCH (m:Model {id: $id}) SET m += $fields", id=model_id, fields=fields)
    if dialect is not None:
        if not isinstance(graph, Neo4jGraph):
            raise SpatialError("making the point index anew needs a Neo4jGraph")
        await rebuild_point_indexes(graph, dialect)
    return {"products": len(products), "meshes": end["meshes"], "failed": end["failed"]}


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
    threads: int = 2,
    limits: Limits = DEFAULT_LIMITS,
) -> dict[str, Any]:
    """Work out the boxes of ``source`` and set them on the model ``model_id``, which was imported from it."""
    with tempfile.TemporaryDirectory(prefix="ifc-lpg-") as tmp:
        records = Path(tmp) / "geometry.jsonl"
        await spatial_file(source, records, threads=threads, limits=limits)
        return await import_spatial(graph, model_id, records, dialect=dialect)


if __name__ == "__main__":
    sys.exit(main())
