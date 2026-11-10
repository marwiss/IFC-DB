# This file was generated with the assistance of an AI coding tool.
"""IFC5 files (IFCX, ``ifcx_alpha``) in the graph and back. Experimental: the format is a draft and can change.

An IFCX file is JSON: a ``header``, ``imports``, ``schemas`` and ``data``, a list of entries that each say something
about a ``path``: its ``children`` and what it ``inherits`` (name to path), and its ``attributes`` (key to JSON value).
One or more files are composed into one opinion per path. A file comes after the files it imports, and each counts
once. A later value for the same path and name wins, ``null`` removes one, and two that disagree are reported. An
import that is none of the files is not fetched, and gives a warning.

Each path is an ``IfcxObject`` node, labelled with its class, the ``code`` of ``bsi::ifc::class``, when that is an
identifier. Its children and what it inherits are ``CHILD`` and ``INHERITS`` relationships with a ``name``. A
``{"ref": path}`` value is a ``REF`` relationship too, named after where it stands; the writer reads the value, not the
relationship. The graph is the same on Neo4j and Memgraph:

- an attribute is a property under its key, and an object one property per member, under dotted keys;
- a list is a list, rows of 2 or 3 numbers a list of points, and other rows of numbers a flat list, with
  ``<key>#shape``; ``<key>#ints`` says which numbers were integers, and ``<key>#null`` that a member is null;
- what does not come back the same is JSON text in ``<key>#json``, and attributes whose keys could be taken for
  these are JSON text in ``#rest``.

A path that is a child or inherited but has no entry is a node with ``#stub``. The model is an ``IfcxModel`` node, with
the header, the imports, the schemas, the warnings and the conflicts. The file is read and written in a process of its
own: ``python -m ifc_lpg.ifcx read FILE... OUT`` and ``python -m ifc_lpg.ifcx write RECORDS OUT``, with ``--memory-mb N``.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import sys
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ifc_lpg.exporter import PAGE
from ifc_lpg.files import DEFAULT_LIMITS, Limits, _now, run_worker
from ifc_lpg.graph import Graph
from ifc_lpg.importer import BATCH, MAX_BATCH_BYTES
from ifc_lpg.lossless import NATIVE

MAX_BYTES = 256 * 1024 * 1024
MAX_DEPTH = 32
PARTS = ("children", "inherits", "attributes")
_META = frozenset(("id", "model_id", "path"))
_PLAIN = re.compile(r"^[^.#]+$")  # a key that cannot be taken for a dotted one or a marker
_POINT_KEY = re.compile(r"^[A-Za-z0-9_:.\-]+$")  # a property that may be named in the text of a query
_LABEL = re.compile(r"^Ifc[A-Za-z0-9_]+$")
_INT64 = 2**63
_EXACT = 2**53  # an integer a double holds exactly
_POINT = "CASE size(p) WHEN 2 THEN point({x: p[0], y: p[1]}) ELSE point({x: p[0], y: p[1], z: p[2]}) END"


class IfcxError(ValueError):
    """The file is no IFCX this reader can read. The message says why."""


class _NoForm(Exception):
    pass


@dataclass
class Composed:
    objects: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    header: dict[str, Any] = field(default_factory=dict)
    imports: list[dict[str, Any]] = field(default_factory=list)
    schemas: dict[str, Any] = field(default_factory=dict)
    layers: list[str] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)


def _refuse(constant: str) -> None:
    raise IfcxError(f"{constant} is no JSON number")


def _deepest(value: Any) -> int:
    """How deep objects and lists nest, counted no further than one past ``MAX_DEPTH``."""
    depth, level = 0, [value]
    while level and depth <= MAX_DEPTH:
        depth += 1
        level = [
            item
            for parent in level
            for item in (parent.values() if isinstance(parent, dict) else parent)
            if isinstance(item, dict | list)
        ]
    return depth


def read_layer(data: bytes) -> dict[str, Any]:
    """One file, refused unless it is IFCX as far as this reader goes."""
    if len(data) > MAX_BYTES:
        raise IfcxError(f"the file is larger than {MAX_BYTES} bytes")
    try:
        document = json.loads(data.decode("utf-8"), parse_constant=_refuse)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise IfcxError(f"the file is no JSON: {str(exc)[:200]}") from None
    if not isinstance(document, dict) or not isinstance(document.get("data"), list):
        raise IfcxError("the file has no list of data")
    if _deepest(document) > MAX_DEPTH:
        raise IfcxError(f"the file nests deeper than {MAX_DEPTH}")
    if not isinstance(document.setdefault("header", {}), dict) or not isinstance(
        document.setdefault("schemas", {}), dict
    ):
        raise IfcxError("the header and the schemas are objects")
    imports = document.setdefault("imports", [])
    if not isinstance(imports, list) or not all(
        isinstance(item, dict) and isinstance(item.get("uri"), str) for item in imports
    ):
        raise IfcxError("each import is an object with a uri")
    for entry in document["data"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise IfcxError("each entry is an object with a path")
        for part in PARTS:
            if not isinstance(entry.get(part) or {}, dict):
                raise IfcxError(f"the {part} of {entry['path'][:80]!r} are no object")
        for part in ("children", "inherits"):
            if not all(target is None or isinstance(target, str) for target in (entry.get(part) or {}).values()):
                raise IfcxError(f"the {part} of {entry['path'][:80]!r} are paths")
    return document


def compose(layers: list[tuple[str, dict[str, Any]]]) -> Composed:
    """The files, named, as one opinion per path."""
    answers: dict[str, int] = {}
    for index, (name, document) in enumerate(layers):
        answers.setdefault(str(document["header"].get("id", name)), index)
        answers.setdefault(name, index)

    def answer(uri: str) -> int | None:
        return answers.get(uri, answers.get(uri.rsplit("/", 1)[-1]))

    order: list[int] = []
    visiting: set[int] = set()

    def visit(index: int) -> None:
        if index in order:
            return
        if index in visiting:
            raise IfcxError("the files import each other in a circle")
        visiting.add(index)
        for item in layers[index][1]["imports"]:
            found = answer(item["uri"])
            if found is not None:
                visit(found)
        order.append(index)

    for index in range(len(layers)):
        visit(index)
    composed = Composed()
    for index in order:
        name, document = layers[index]
        layer = str(document["header"].get("id", name))
        composed.layers.append(layer)
        composed.header = document["header"]
        for item in document["imports"]:
            if answer(item["uri"]) is None and item not in composed.imports:
                composed.imports.append(item)
                composed.warnings.append({"kind": "import_unavailable", "uri": item["uri"], "layer": layer})
        for key, value in document["schemas"].items():
            if key in composed.schemas and not same(composed.schemas[key], value):
                composed.warnings.append({"kind": "schema_override", "key": key, "layer": layer})
            composed.schemas[key] = value
        for entry in document["data"]:
            found = composed.objects.setdefault(entry["path"], {part: {} for part in PARTS})
            for part in PARTS:
                for key, value in (entry.get(part) or {}).items():
                    if key in found[part] and not same(found[part][key], value):
                        composed.conflicts.append({"path": entry["path"], "part": part, "name": key, "layer": layer})
                    if value is None:
                        found[part].pop(key, None)
                    else:
                        found[part][key] = value
    return composed


def same(a: Any, b: Any) -> bool:
    """The same JSON, with the same types: an integer is no float, and a float is the same to the bit."""
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def _number(value: Any) -> bool:
    return type(value) is int or type(value) is float


def _flatten(prefix: str, value: Any, out: dict[str, Any], points: dict[str, list[list[float]]]) -> None:
    if isinstance(value, bool | str | float):
        out[prefix] = value
    elif isinstance(value, int):
        if not -_INT64 <= value < _INT64:
            raise _NoForm
        out[prefix] = value
    elif value is None:
        out[prefix + "#null"] = True
    elif isinstance(value, dict):
        if not value or not all(_PLAIN.match(key) for key in value):
            raise _NoForm
        for key, item in value.items():
            _flatten(f"{prefix}.{key}", item, out, points)
    elif isinstance(value, list):
        _flatten_list(prefix, value, out, points)
    else:
        raise _NoForm


def _flatten_list(prefix: str, value: list[Any], out: dict[str, Any], points: dict[str, list[list[float]]]) -> None:
    for kind in (str, bool, int, float):
        if all(type(item) is kind for item in value):
            if kind is int and not all(-_INT64 <= item < _INT64 for item in value):
                raise _NoForm
            out[prefix] = value
            return
    nested = all(isinstance(row, list) for row in value)
    rows = value if nested else [value]
    columns = len(rows[0])
    if not columns or any(len(row) != columns for row in rows):
        raise _NoForm
    flat = [item for row in rows for item in row]
    ints = [index for index, item in enumerate(flat) if type(item) is int]
    if not all(_number(item) for item in flat) or any(abs(flat[index]) > _EXACT for index in ints):
        raise _NoForm
    as_points = nested and columns in (2, 3) and _POINT_KEY.match(prefix) is not None
    if as_points or 0 < len(ints) < len(flat):  # a point holds doubles, and so does a list that mixes them
        flat = [float(item) for item in flat]
        if ints:
            out[prefix + "#ints"] = ints
    if as_points:
        points[prefix] = [flat[start : start + columns] for start in range(0, len(flat), columns)]
    else:
        out[prefix] = flat
        if nested:
            out[prefix + "#shape"] = [len(rows), columns]


def encode_attributes(attributes: dict[str, Any]) -> tuple[dict[str, Any], dict[str, list[list[float]]]]:
    """The properties of a node, and its lists of points apart, which the importer makes points of."""
    props: dict[str, Any] = {}
    points: dict[str, list[list[float]]] = {}
    rest: dict[str, Any] = {}
    for key, value in attributes.items():
        if not _PLAIN.match(key) or key in _META:
            rest[key] = value
            continue
        flat: dict[str, Any] = {}
        found: dict[str, list[list[float]]] = {}
        try:
            _flatten(key, value, flat, found)
            back = decode_attributes({**flat, **found})
        except _NoForm:
            back = None
        if back is not None and list(back) == [key] and same(back[key], value):
            props.update(flat)
            points.update(found)
        else:
            props[key + "#json"] = json.dumps(value)
    if rest:
        props["#rest"] = json.dumps(rest)
    return props, points


def decode_attributes(props: dict[str, Any]) -> dict[str, Any]:
    """The attributes back from what a node holds, with points as rows of numbers."""
    attributes: dict[str, Any] = json.loads(props["#rest"]) if "#rest" in props else {}
    markers: dict[str, dict[str, Any]] = {}
    for name, value in props.items():
        base, marked, kind = name.partition("#")
        if marked and base:
            markers.setdefault(base, {})[kind] = value
    for name, value in props.items():
        if name in _META or "#" in name:
            continue
        if isinstance(value, dict) and NATIVE in value:
            raise ValueError(f"{name} holds a {value[NATIVE]}, which is no value of IFCX")
        kinds = markers.get(name, {})
        if isinstance(value, list):
            nested = bool(value) and isinstance(value[0], list | tuple)  # points, as the graph gives them back
            flat = [item for row in value for item in row] if nested else list(value)
            for index in kinds.get("ints", ()):
                flat[index] = int(flat[index])
            columns = kinds["shape"][1] if "shape" in kinds else len(value[0]) if nested else 0
            value = [flat[start : start + columns] for start in range(0, len(flat), columns)] if columns else flat
        _put(attributes, name, value)
    for base, kinds in markers.items():
        if "json" in kinds:
            _put(attributes, base, json.loads(kinds["json"]))
        elif "null" in kinds:
            _put(attributes, base, None)
    return attributes


def _put(attributes: dict[str, Any], dotted: str, value: Any) -> None:
    *outer, last = dotted.split(".")
    for key in outer:
        attributes = attributes.setdefault(key, {})
    attributes[last] = value


def _refs(value: Any, trail: str) -> Iterator[tuple[str, str]]:
    if isinstance(value, dict):
        if set(value) == {"ref"} and isinstance(value["ref"], str):
            yield trail, value["ref"]
        else:
            for key, item in value.items():
                yield from _refs(item, f"{trail}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _refs(item, f"{trail}[{index}]")


def records(composed: Composed) -> Iterator[dict[str, Any]]:
    """A header record, then node records, then edge records, as ``import_records`` reads them."""
    yield {
        "t": "h",
        "header": composed.header,
        "imports": composed.imports,
        "schemas": composed.schemas,
        "layers": composed.layers,
        "warnings": composed.warnings,
        "conflicts": composed.conflicts,
        "objects": len(composed.objects),
    }
    stubs: set[str] = set()
    for path, found in composed.objects.items():
        props, points = encode_attributes(found["attributes"])
        code = found["attributes"].get("bsi::ifc::class")
        code = code.get("code") if isinstance(code, dict) else None
        labels = [code] if isinstance(code, str) and _LABEL.match(code) else []
        yield {"t": "n", "path": path, "labels": labels, "props": props, "points": points}
        stubs.update(target for part in ("children", "inherits") for target in found[part].values())
    for path in sorted(stubs - composed.objects.keys()):
        yield {"t": "n", "path": path, "labels": [], "props": {"#stub": True}, "points": {}}
    for path, found in composed.objects.items():
        for part, kind in (("children", "CHILD"), ("inherits", "INHERITS")):
            for name, target in found[part].items():
                yield {"t": "e", "type": kind, "a": path, "b": target, "name": name}
        for key, value in found["attributes"].items():
            for trail, target in _refs(value, key):
                if target in composed.objects:
                    yield {"t": "e", "type": "REF", "a": path, "b": target, "name": trail}


def document_of(model: dict[str, Any], nodes: Iterator[dict[str, Any]]) -> dict[str, Any]:
    """The file, one entry per path in order of path, from the model and its nodes as ``export_records`` gives them."""
    data = []
    for node in sorted(nodes, key=lambda node: node["path"]):
        if node["props"].get("#stub"):
            continue
        entry: dict[str, Any] = {"path": node["path"]}
        for part, kind in (("children", "CHILD"), ("inherits", "INHERITS")):
            links = {name: target for link_kind, name, target in node["links"] if link_kind == kind}
            if links:
                entry[part] = links
        try:
            attributes = decode_attributes(node["props"])
        except ValueError as exc:
            raise ValueError(f"{node['path'][:80]}: {exc}") from None
        if attributes:
            entry["attributes"] = attributes
        data.append(entry)
    return {"header": model["header"], "imports": model["imports"], "schemas": model["schemas"], "data": data}


async def import_records(graph: Graph, model_id: str, records_path: Path) -> dict[str, int]:
    """Create the nodes and relationships of the records for the model ``model_id``. Returns counts."""
    nodes = relationships = 0
    groups: dict[tuple[tuple[str, ...], tuple[str, ...]], list[dict[str, Any]]] = {}
    edges: dict[str, list[dict[str, Any]]] = {}

    async def flush() -> None:
        nonlocal nodes, relationships
        for (labels, keys), rows in groups.items():
            label_clause = ":".join(["IfcxObject", *labels])
            points = "".join(f", n.`{key}` = [p IN row.points[{i}] | {_POINT}]" for i, key in enumerate(keys))
            await graph.run(
                f"UNWIND $rows AS row CREATE (n:{label_clause}) SET n = row.props, n.id = row.id, n.path = row.path, "
                f"n.model_id = $model_id{points}",
                rows=rows,
                model_id=model_id,
            )
            nodes += len(rows)
        groups.clear()
        for kind, rows in edges.items():
            await graph.run(
                f"UNWIND $rows AS row MATCH (a:IfcxObject {{id: row.a}}), (b:IfcxObject {{id: row.b}}) "
                f"CREATE (a)-[:{kind} {{name: row.name}}]->(b)",
                rows=rows,
            )
            relationships += len(rows)
        edges.clear()

    pending = pending_bytes = 0
    with records_path.open(encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            if record["t"] == "n":
                labels = tuple(label for label in record["labels"] if _LABEL.match(label))
                keys = tuple(key for key in sorted(record["points"]) if _POINT_KEY.match(key))
                row = {
                    "id": f"{model_id}:{record['path']}",
                    "path": record["path"],
                    "props": record["props"],
                    "points": [record["points"][key] for key in keys],
                }
                groups.setdefault((labels, keys), []).append(row)
            elif record["t"] == "e" and record["type"] in ("CHILD", "INHERITS", "REF"):
                if groups:
                    await flush()  # every node before the first relationship
                edges.setdefault(record["type"], []).append(
                    {"a": f"{model_id}:{record['a']}", "b": f"{model_id}:{record['b']}", "name": record["name"]}
                )
            else:
                continue
            pending += 1
            pending_bytes += len(line)
            if pending >= BATCH or pending_bytes >= MAX_BATCH_BYTES:
                await flush()
                pending = pending_bytes = 0
    await flush()
    return {"objects": nodes, "relationships": relationships}


async def export_records(graph: Graph, model_id: str, out: Path) -> int:
    """Write the model and its nodes to ``out``, for the writer. Returns the number of nodes."""
    rows = await graph.run(
        "MATCH (m:IfcxModel {id: $id}) RETURN m.state AS state, m.header AS header, m.imports AS imports, "
        "m.schemas AS schemas",
        id=model_id,
    )
    if not rows:
        raise ValueError("there is no such model")
    if rows[0]["state"] != "done":
        raise ValueError(f"the model is {rows[0]['state']}, not done")
    model = {key: json.loads(rows[0][key]) for key in ("header", "imports", "schemas")}
    paths = sorted(
        row["path"]
        for row in await graph.run("MATCH (n:IfcxObject {model_id: $id}) RETURN n.path AS path", id=model_id)
    )
    with out.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(model) + "\n")
        for start in range(0, len(paths), PAGE):
            page = await graph.run(
                """
                UNWIND $ids AS node_id
                MATCH (n:IfcxObject {id: node_id})
                OPTIONAL MATCH (n)-[r:CHILD|INHERITS]->(t:IfcxObject)
                WITH n, CASE WHEN r IS NULL THEN NULL ELSE [type(r), r.name, t.path] END AS link
                RETURN n.path AS path, properties(n) AS props, collect(link) AS links
                """,
                ids=[f"{model_id}:{path}" for path in paths[start : start + PAGE]],
            )
            for row in page:
                handle.write(json.dumps(row, default=lambda value: {NATIVE: type(value).__name__}) + "\n")
    return len(paths)


def write_records(records_path: Path, target: Path) -> None:
    with records_path.open(encoding="utf-8") as handle:
        model = json.loads(next(handle))
        document = document_of(model, (json.loads(line) for line in handle))
    target.write_text(json.dumps(document, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


async def delete_ifcx(graph: Graph, model_id: str, *, keep_model: bool = False) -> None:
    """Remove a model's nodes in batches, and the ``IfcxModel`` itself unless ``keep_model``."""
    while True:
        rows = await graph.run(
            "MATCH (n:IfcxObject {model_id: $id}) WITH n LIMIT $batch DETACH DELETE n RETURN count(*) AS n",
            id=model_id,
            batch=BATCH,
        )
        if not rows or int(rows[0]["n"]) < BATCH:
            break
    if not keep_model:
        await graph.run("MATCH (m:IfcxModel {id: $id}) DETACH DELETE m", id=model_id)


async def import_ifcx(
    graph: Graph, sources: list[Path], model_id: str, *, limits: Limits = DEFAULT_LIMITS
) -> dict[str, Any]:
    """Compose ``sources`` and load them as the model ``model_id``, which must not exist yet. As ``import_file`` does
    for STEP: ``running`` while it loads, ``done`` with its counts, or ``failed`` with the error and nothing left."""
    rows = await graph.run(
        "MERGE (m:IfcxModel {id: $id}) ON CREATE SET m.state = 'running', m.running_at = $now, m.file_names = $names "
        "RETURN m.running_at = $now AS new",
        id=model_id,
        now=_now(),
        names=[source.name for source in sources],
    )
    if not rows or not rows[0]["new"]:
        raise ValueError(f"the model {model_id!r} exists already")
    try:
        with tempfile.TemporaryDirectory(prefix="ifc-lpg-") as tmp:
            out = Path(tmp) / "records.jsonl"
            command = [sys.executable, "-m", "ifc_lpg.ifcx", "read", *map(str, sources), str(out)]
            command += ["--memory-mb", str(limits.memory_mb), "--max-entries", str(limits.max_entities)]
            header = json.loads((await run_worker(command, "reading IFCX", limits.timeout_seconds)).splitlines()[-1])
            counts = await import_records(graph, model_id, out)
    except Exception as exc:
        with contextlib.suppress(Exception):
            await delete_ifcx(graph, model_id, keep_model=True)
        await graph.run(
            "MATCH (m:IfcxModel {id: $id}) SET m.state = 'failed', m.failed_at = $now, m.error = $error",
            id=model_id,
            now=_now(),
            error=f"{type(exc).__name__}: {exc}"[:500],
        )
        raise
    fields: dict[str, Any] = {key: json.dumps(header[key]) for key in ("header", "imports", "schemas")}
    fields |= {key: json.dumps(header[key]) for key in ("warnings", "conflicts")}
    fields |= {"layers": header["layers"], **counts, "done_at": _now()}
    await graph.run("MATCH (m:IfcxModel {id: $id}) SET m.state = 'done', m += $fields", id=model_id, fields=fields)
    return {"id": model_id, "state": "done", **fields}


async def export_ifcx(graph: Graph, model_id: str, target: Path, *, limits: Limits = DEFAULT_LIMITS) -> int:
    """Write the model ``model_id`` as an IFCX file at ``target``. Returns the number of entries' nodes read."""
    with tempfile.TemporaryDirectory(prefix="ifc-lpg-") as tmp:
        records_path = Path(tmp) / "records.jsonl"
        count = await export_records(graph, model_id, records_path)
        command = [sys.executable, "-m", "ifc_lpg.ifcx", "write", str(records_path), str(target)]
        await run_worker([*command, "--memory-mb", str(limits.memory_mb)], "writing IFCX", limits.timeout_seconds)
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ifc-lpg-ifcx")
    modes = parser.add_subparsers(dest="mode", required=True)
    read = modes.add_parser("read", help="compose IFCX files into records")
    read.add_argument("paths", type=Path, nargs="+", help="the files, then where the records go")
    read.add_argument("--max-entries", type=int, default=5_000_000)
    write = modes.add_parser("write", help="write exported records as an IFCX file")
    write.add_argument("paths", type=Path, nargs=2, help="the records, then the file")
    for mode in (read, write):
        mode.add_argument("--memory-mb", type=int, default=0)
    args = parser.parse_args(argv)
    from ifc_lpg.worker import limit_memory

    if args.memory_mb:
        limit_memory(args.memory_mb)
    *sources, out = args.paths
    try:
        if args.mode == "write":
            write_records(sources[0], out)
            return 0
        if not sources:
            raise IfcxError("no file to read")
        layers = []
        for source in sources:
            if source.stat().st_size > MAX_BYTES:
                raise IfcxError(f"{source.name} is larger than {MAX_BYTES} bytes")
            layers.append((source.name, read_layer(source.read_bytes())))
        if sum(len(document["data"]) for _name, document in layers) > args.max_entries:
            raise IfcxError(f"the files have more than {args.max_entries} entries")
        composed = compose(layers)
        with out.open("w", encoding="utf-8") as handle:
            for record in records(composed):
                handle.write(json.dumps(record) + "\n")
        header = {key: value for key, value in next(records(composed)).items() if key != "t"}
        sys.stdout.write(json.dumps(header) + "\n")
    except (IfcxError, ValueError, KeyError, OSError) as exc:
        sys.stderr.write(json.dumps({"error": f"{type(exc).__name__}: {str(exc)[:500]}"}) + "\n")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
