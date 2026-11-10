# This file was generated with the assistance of an AI coding tool.
"""IFC5 files through a real graph database: the same composition back, and the same Cypher on Neo4j and Memgraph."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ifc_lpg.ifcx import compose, export_ifcx, import_ifcx, read_layer, same
from tests.test_ifcx import BASE, BODY, OVERLAY, SPACE, WALL, write

pytestmark = pytest.mark.integration
pytest.importorskip("neo4j", reason="the neo4j extra is not installed")


async def test_two_files_come_back_as_the_same_composition(graph: Any, tmp_path: Path) -> None:
    sources = [write(tmp_path, "overlay.ifcx", OVERLAY), write(tmp_path, "base.ifcx", BASE)]
    model = await import_ifcx(graph, sources, "hello")
    assert model["state"] == "done" and model["objects"] == 6
    assert model["layers"] == ["example/base.ifcx", "example/overlay.ifcx"]
    target = tmp_path / "back.ifcx"
    assert await export_ifcx(graph, "hello", target) == 6
    again = compose([("back.ifcx", read_layer(target.read_bytes()))])
    original = compose([("overlay.ifcx", OVERLAY), ("base.ifcx", BASE)])
    assert same(again.objects, original.objects), "the same children, inherits and attributes, with their types"
    with pytest.raises(ValueError, match="exists already"):
        await import_ifcx(graph, sources, "hello")


async def test_the_graph_answers_with_the_same_cypher_on_both(graph: Any, tmp_path: Path) -> None:
    await import_ifcx(graph, [write(tmp_path, "base.ifcx", BASE)], "hello")
    walls = await graph.run(
        "MATCH (w:IfcWall {model_id: 'hello'}) WHERE w.`bsi::ifc::prop::IsExternal` AND w.`bsi::ifc::prop::Height` > 2 "
        "RETURN w.path AS path, w.`bsi::ifc::class.code` AS code"
    )
    assert walls == [{"path": WALL, "code": "IfcWall"}]
    tree = await graph.run(
        "MATCH (:IfcProject {model_id: 'hello'})-[:CHILD*]->(w:IfcWall)-[:INHERITS {name: 'type'}]->()"
        "-[:CHILD {name: 'Body'}]->(b:IfcxObject) "
        "RETURN b.path AS body, size(b.`usd::usdgeom::mesh.points`) AS vertices, "
        "b.`usd::usdgeom::mesh.points`[2].x AS x, b.`usd::usdgeom::mesh.points`[0].srid AS srid"
    )
    assert tree == [{"body": BODY, "vertices": 3, "x": 1.7976931348623157e308, "srid": 9157}]
    near = await graph.run(
        "MATCH (b:IfcxObject {model_id: 'hello'}) WHERE b.`usd::usdgeom::mesh.points` IS NOT NULL "
        "RETURN [p IN b.`usd::usdgeom::mesh.points` WHERE point.distance(p, point({x: 0.0, y: 0.6, z: 3.0})) < 0.01 "
        "| p.z] AS near"
    )
    assert near == [{"near": [3.0]}]
    refs = await graph.run(
        "MATCH (:IfcWall {model_id: 'hello'})-[r:REF]->(s:IfcSpace) RETURN r.name AS name, s.path AS s"
    )
    assert refs == [{"name": "example::boundary.relating", "s": SPACE}]
    stub = await graph.run("MATCH (n:IfcxObject {model_id: 'hello', path: 'nowhere'}) RETURN n.`#stub` AS stub")
    assert stub == [{"stub": True}]


async def test_a_file_that_cannot_be_read_leaves_nothing_behind(graph: Any, tmp_path: Path) -> None:
    broken = tmp_path / "broken.ifcx"
    broken.write_bytes(b'{"data": [{"path": "a"}, ')
    with pytest.raises(RuntimeError, match="no JSON"):
        await import_ifcx(graph, [broken], "broken")
    rows = await graph.run("MATCH (m:IfcxModel {id: 'broken'}) RETURN m.state AS state")
    assert rows == [{"state": "failed"}]
    assert (await graph.run("MATCH (n:IfcxObject {model_id: 'broken'}) RETURN count(n) AS n")) == [{"n": 0}]
