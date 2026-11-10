# This file was generated with the assistance of an AI coding tool.
"""A building of one model into another's project, in a real graph: what comes along, the choices, and the check."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ifc_lpg.equivalence import compare_files
from ifc_lpg.files import export_file, import_file
from ifc_lpg.merge import MergeConflict, MergeError, merge_building, merge_report
from tests.conftest import FIXTURES_DIR

pytestmark = pytest.mark.integration
pytest.importorskip("ifcopenshell", reason="IfcOpenShell is not installed")
pytest.importorskip("neo4j", reason="the neo4j extra is not installed")

SPATIAL = FIXTURES_DIR / "spatial.ifc"
ANNEX = FIXTURES_DIR / "annex.ifc"
BUILDING = "0Spatial00000000000003"
ANNEX_BUILDING = "0Annex0000000000000003"
WALL = "0Spatial00000000000010"


async def names(graph: Any, query: str) -> list[Any]:
    return [row["v"] for row in await graph.run(query)]


async def comes_back(graph: Any, model_id: str, tmp_path: Path) -> Path:
    """The model written out, read in again and written out again gives the same file."""
    first, second = tmp_path / f"{model_id}.ifc", tmp_path / f"{model_id}-again.ifc"
    await export_file(graph, model_id, first)
    await import_file(graph, first, f"{model_id}-again")
    await export_file(graph, f"{model_id}-again", second)
    assert compare_files(first, second).identical
    return first


async def test_its_own_building_changes_nothing(graph: Any, tmp_path: Path) -> None:
    await import_file(graph, SPATIAL, "a")
    report = await merge_building(graph, "a", "a", BUILDING, "c")
    assert {(c["same"], c["choice"]) for c in report["collisions"]} == {(True, "a")}
    assert report["model"]["state"] == "done"
    back = tmp_path / "back.ifc"
    await export_file(graph, "c", back)
    assert compare_files(SPATIAL, back).identical, "every object is A's own"


async def test_its_own_building_again_as_new_objects(graph: Any, tmp_path: Path) -> None:
    await import_file(graph, SPATIAL, "a")
    report = await merge_report(graph, "a", "a", BUILDING)
    assert report["objects"] == 9 and len(report["collisions"]) == 14
    choices = {c["global_id"]: "new" for c in report["collisions"]}
    merged = await merge_building(graph, "a", "a", BUILDING, "c", choices=choices)
    assert await names(
        graph,
        "MATCH (:IfcSite {model_id: 'c'})<-[:RELATING_OBJECT]-(:IfcRelAggregates)-[:RELATED_OBJECTS]->(b:IfcBuilding) "
        "RETURN b.Name AS v",
    ) == ["Building", "Building"]
    rows = await graph.run(
        "MATCH (n:Entity {model_id: 'c'}) WHERE n.GlobalId IS NOT NULL "
        "RETURN count(n) AS n, count(DISTINCT n.GlobalId) AS ids"
    )
    assert rows[0]["n"] == rows[0]["ids"] == 21 + 14 + 1, "the copies and their aggregation, each with its own GlobalId"
    assert await names(graph, "MATCH (n:IfcBuildingElementProxy {model_id: 'c'}) RETURN n.Name AS v ORDER BY v") == [
        "Broken",
        "Outside",
    ], "what stands on the site and not in the building stays one"
    assert await names(
        graph,
        "MATCH (:IfcColumn {model_id: 'c'})-[:REPRESENTATION]->()-[:REPRESENTATIONS]->()-[:ITEMS]->()"
        "-[:MAPPING_SOURCE]->(m) RETURN count(DISTINCT m) AS v",
    ) == [2], "the columns of each building share their map"
    assert await names(graph, "MATCH (s:IfcStyledItem {model_id: 'c'}) RETURN count(s) AS v") == [2]
    assert merged["model"]["entities"] > 0
    await comes_back(graph, "c", tmp_path)


async def test_the_annex_comes_with_what_concerns_it(graph: Any, tmp_path: Path) -> None:
    await import_file(graph, SPATIAL, "a")
    await import_file(graph, ANNEX, "b")
    report = await merge_report(graph, "a", "b", ANNEX_BUILDING)
    assert report["collisions"] == [{"global_id": WALL, "ifc_class": "IfcWall", "same": False, "choice": None}]
    assert report["left_out"] == [{"global_id": "0Annex0000000000000033", "ifc_class": "IfcRelAssignsToGroup"}]
    assert (report["objects"], report["contexts"], report["placements"]) == (4, {"shared": 1, "added": 1}, 1)
    with pytest.raises(MergeConflict, match=WALL) as raised:
        await merge_building(graph, "a", "b", ANNEX_BUILDING, "c")
    assert raised.value.report["collisions"][0]["choice"] is None
    assert await graph.run("MATCH (m:Model {id: 'c'}) RETURN m.id AS id") == [], "nothing is made before the choice"

    await merge_building(graph, "a", "b", ANNEX_BUILDING, "c", choices={WALL: "new"})
    assert await names(graph, "MATCH (w:IfcWall {model_id: 'c'}) RETURN w.Name AS v ORDER BY v") == [
        "Annex wall",
        "Wall",
    ]
    placed = await graph.run(
        "MATCH (:IfcBuilding {model_id: 'c', Name: 'Annex'})-[:OBJECT_PLACEMENT]->()-[:PLACEMENT_REL_TO]->(p) "
        "MATCH (:IfcSite {model_id: 'c'})-[:OBJECT_PLACEMENT]->(s) RETURN p = s AS v"
    )
    assert placed == [{"v": True}], "below A's site as it was below its own"
    assert await names(
        graph, "MATCH (:IfcRelDefinesByProperties {model_id: 'c'})-[:RELATED_OBJECTS]->(o) RETURN o.Name AS v"
    ) == ["Annex wall"], "the property set comes along for what is inside alone"
    assert (
        await names(
            graph,
            "MATCH (n:Entity {model_id: 'c'}) WHERE n.Name IN ['Shed', 'Phase 2', 'Annex site'] RETURN n.Name AS v",
        )
        == []
    )
    assert await names(
        graph, "MATCH (:IfcPresentationLayerAssignment {model_id: 'c'})-[:ASSIGNED_ITEMS]->(i) RETURN count(i) AS v"
    ) == [1], "the layer keeps the annex wall and not the shed"
    assert await graph.run(
        "MATCH (:IfcProject {model_id: 'c'})-[r:REPRESENTATION_CONTEXTS]->(c) "
        "RETURN r.position AS position, c.ContextType AS type ORDER BY position"
    ) == [{"position": 0, "type": "Model"}, {"position": 1, "type": "Plan"}], "A's context, and the one it lacked"
    assert await names(
        graph,
        "MATCH (:IfcBuildingStorey {model_id: 'c', Name: 'Annex floor'})-[:CONTAINS_ELEMENTS]->()"
        "-[:RELATED_ELEMENTS]->(e) RETURN e.Name AS v ORDER BY v",
    ) == ["Annex column", "Annex wall"], "the inverse relationships are made anew"
    await comes_back(graph, "c", tmp_path)


async def test_a_choice_for_a_or_b_keeps_one_wall(graph: Any, tmp_path: Path) -> None:
    await import_file(graph, SPATIAL, "a")
    await import_file(graph, ANNEX, "b")
    await merge_building(graph, "a", "b", ANNEX_BUILDING, "ca", choices={WALL: "a"})
    assert await names(graph, "MATCH (w:IfcWall {model_id: 'ca'}) RETURN w.Name AS v") == ["Wall"]
    assert await names(
        graph,
        "MATCH (:IfcBuildingStorey {model_id: 'ca', Name: 'Annex floor'})-[:CONTAINS_ELEMENTS]->()"
        "-[:RELATED_ELEMENTS]->(e) RETURN e.Name AS v",
    ) == ["Annex column"], "A's wall stays where A has it"
    await merge_building(graph, "a", "b", ANNEX_BUILDING, "cb", choices={WALL: "b"})
    assert await names(graph, "MATCH (w:IfcWall {model_id: 'cb'}) RETURN w.Name AS v") == ["Annex wall"]
    assert await names(
        graph,
        "MATCH (w:IfcWall {model_id: 'cb'})-[:HAS_OPENINGS]->()-[:RELATED_OPENING_ELEMENT]->(o) RETURN o.Name AS v",
    ) == ["Opening"], "what of A refers to the wall refers to B's"
    assert await names(
        graph,
        "MATCH (w:IfcWall {model_id: 'cb'})-[:CONTAINED_IN_STRUCTURE]->()-[:RELATING_STRUCTURE]->(s) RETURN s.Name AS v",
    ) == ["Annex floor"], "and it is in B's storey alone"
    await comes_back(graph, "cb", tmp_path)


async def test_other_units_or_another_schema_are_refused(graph: Any, tmp_path: Path) -> None:
    await import_file(graph, SPATIAL, "a")
    millimetres = tmp_path / "annex-mm.ifc"
    millimetres.write_bytes(
        ANNEX.read_bytes().replace(b"(*,.LENGTHUNIT.,$,.METRE.)", b"(*,.LENGTHUNIT.,.MILLI.,.METRE.)")
    )
    await import_file(graph, millimetres, "mm")
    with pytest.raises(MergeError, match="units differ"):
        await merge_report(graph, "a", "mm", ANNEX_BUILDING)
    await import_file(graph, FIXTURES_DIR / "three_walls_gtin.ifc", "old")
    with pytest.raises(MergeError, match="schema of A is IFC4 and of B IFC2X3"):
        await merge_report(graph, "a", "old", ANNEX_BUILDING)
