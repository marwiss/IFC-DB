# This file was generated with the assistance of an AI coding tool.
"""What every fixture becomes in the graph, and what is read back from it, kept as snapshots.

For each fixture and each of the three forms: every node and relationship, the records written from the graph, the
path that names every instance, what no GlobalId reaches, and the answer of every named query and of
``property_values``. The same snapshot holds on Neo4j and on Memgraph, so moving the database code must leave all of it
as it is. ``IFC_DB_WRITE_SNAPSHOTS=1`` writes them anew from the database the run uses.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from ifc_db.exporter import export_records
from ifc_db.files import import_file
from ifc_db.paths import PathError, canonical_path, unreachable
from ifc_db.queries import QUERIES, NamedQuery, property_values, run_query
from tests.conftest import FIXTURES_DIR

pytestmark = pytest.mark.integration
pytest.importorskip("ifcopenshell", reason="IfcOpenShell is not installed")
pytest.importorskip("neo4j", reason="the neo4j extra is not installed")

SNAPSHOTS = FIXTURES_DIR / "snapshots"
FIXTURES = sorted(path.stem for path in FIXTURES_DIR.glob("*.ifc"))
FORMS = {"skeleton": {}, "nodes": {"include_geometry": True}, "blocks": {"geometry_in_blocks": True}}
CLASSES = ("IfcProduct", "IfcWall", "IfcSpace")
OWN_LABELS = ("Model", "StepBlock", "Entity", "Object")
MODEL = "m"
UNORDERED = {"references", "referenced_by", "materials"}  # the lists a database collects in no given order


def _plain(value: Any) -> Any:
    """A value as JSON keeps it, with a point's reference system beside its coordinates."""
    srid = getattr(value, "srid", None)
    if srid is not None and isinstance(value, tuple):
        return {"srid": srid, "point": list(value)}
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    return value


def _key(labels: list[str], properties: dict[str, Any]) -> str:
    own = next((label for label in OWN_LABELS if label in labels), "+".join(sorted(labels)))
    return f"{own} {properties.get('id')}"


async def _graph(graph: Any) -> dict[str, Any]:
    """Every node and relationship in the database, which holds this one model, without the times of day."""
    nodes = await graph.run("MATCH (n) RETURN labels(n) AS labels, properties(n) AS properties")
    edges = await graph.run(
        "MATCH (a)-[r]->(b) RETURN labels(a) AS a_labels, a.id AS a, type(r) AS type, properties(r) AS properties, "
        "labels(b) AS b_labels, b.id AS b"
    )
    kept = {
        _key(row["labels"], row["properties"]): {
            "labels": sorted(row["labels"]),
            "properties": {key: _plain(item) for key, item in row["properties"].items() if not key.endswith("_at")},
        }
        for row in nodes
    }
    assert len(kept) == len(nodes), "every node has a key of its own"
    relationships = sorted(
        json.dumps(
            [
                _key(row["a_labels"], {"id": row["a"]}),
                row["type"],
                _plain(row["properties"]),
                _key(row["b_labels"], {"id": row["b"]}),
            ],
            sort_keys=True,
        )
        for row in edges
    )
    return {"nodes": kept, "relationships": relationships}


def _record(line: str) -> str:
    """A record by what it holds: the database gives a node's properties and references in an order of its own, and
    the writer reads them by name and position."""
    record = json.loads(line)
    if "references" in record:
        record["references"] = sorted(record["references"], key=json.dumps)
    return json.dumps(record, sort_keys=True)


def _text(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


def _ordered(answer: dict[str, Any]) -> dict[str, Any]:
    rows = [
        [
            sorted(cell, key=_text) if column in UNORDERED and isinstance(cell, list) else cell
            for column, cell in zip(answer["columns"], row, strict=True)
        ]
        for row in answer["rows"]
    ]
    return {**answer, "rows": rows}


def _parameter_sets(query: NamedQuery, global_ids: list[str], pairs: list[tuple[str, str]]) -> list[dict[str, str]]:
    names = {parameter.name for parameter in query.parameters}
    required = {parameter.name for parameter in query.parameters if parameter.required}
    assert required <= {"global_id", "class", "pset", "property"}, f"{query.name} needs a value the snapshot lacks"
    if "global_id" in required:
        return [{"global_id": global_id} for global_id in global_ids]
    if "class" in required:
        return [{"class": name} for name in CLASSES]
    sets: list[dict[str, str]] = [{"pset": pset, "property": name} for pset, name in pairs] if required else [{}]
    if "class" in names:
        sets += [{**values, "class": "IfcWall"} for values in sets]
    return sets


async def _answers(graph: Any) -> dict[str, Any]:
    roots = await graph.run(
        "MATCH (n:Entity {model_id: $m}) WHERE n.GlobalId IS NOT NULL RETURN n.GlobalId AS g ORDER BY g", m=MODEL
    )
    global_ids = [row["g"] for row in roots]
    found = await graph.run(
        "MATCH (s:IfcPropertySet {model_id: $m})-[:HAS_PROPERTIES]->(p:IfcProperty) "
        "RETURN DISTINCT s.Name AS pset, p.Name AS name ORDER BY pset, name",
        m=MODEL,
    )
    pairs = [(row["pset"], row["name"]) for row in found if row["pset"] and row["name"]]
    queries = {}
    for name in sorted(QUERIES):
        for values in _parameter_sets(QUERIES[name], global_ids, pairs):
            answer = await run_query(graph, MODEL, name, values)
            queries[json.dumps([name, values], sort_keys=True)] = _ordered(_plain(answer))
    properties = {
        f"{pset} {name}": await property_values(graph, MODEL, pset=pset, property=name, global_ids=global_ids[:100])
        for pset, name in pairs
    }
    return {"queries": queries, "property_values": properties}


async def _paths(graph: Any, steps: list[int]) -> dict[str, Any]:
    named: dict[str, Any] = {}
    for step in steps:
        try:
            named[str(step)] = await canonical_path(graph, MODEL, step)
        except PathError:
            named[str(step)] = None
    return {"paths": named, "unreachable": await unreachable(graph, MODEL)}


@pytest.mark.parametrize("form", list(FORMS))
@pytest.mark.parametrize("fixture", FIXTURES)
async def test_the_graph_and_what_is_read_from_it_stay_as_they_were(
    graph: Any, fixture: str, form: str, tmp_path: Path
) -> None:
    await import_file(graph, FIXTURES_DIR / f"{fixture}.ifc", MODEL, **FORMS[form])
    records = tmp_path / "records.jsonl"
    await export_records(graph, MODEL, records)
    lines = records.read_text(encoding="utf-8").splitlines()
    steps = [record["step"] for record in map(json.loads, lines) if "step" in record]
    seen = {
        "graph": await _graph(graph),
        "records": [_record(line) for line in lines],
        **await _paths(graph, steps),
        **await _answers(graph),
    }
    snapshot = SNAPSHOTS / f"{fixture}-{form}.json"
    if os.environ.get("IFC_DB_WRITE_SNAPSHOTS") == "1":
        SNAPSHOTS.mkdir(exist_ok=True)
        snapshot.write_text(json.dumps(seen, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    assert snapshot.exists(), "write the snapshots with IFC_DB_WRITE_SNAPSHOTS=1"
    expected = json.loads(snapshot.read_text(encoding="utf-8"))
    parts = ("graph", "records", "paths", "unreachable", "queries", "property_values")
    differ = [
        part for part in parts if json.dumps(seen[part], sort_keys=True) != json.dumps(expected[part], sort_keys=True)
    ]
    if differ:
        found = tmp_path / snapshot.name
        found.write_text(json.dumps(seen, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        pytest.fail(f"not as before: {differ}; what was found is in {found}")
