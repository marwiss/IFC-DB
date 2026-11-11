# This file was generated with the assistance of an AI coding tool.
"""A building of one model into another's project, in a real graph: what comes along, the choices, and the check."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ifc_lpg.equivalence import compare_files
from ifc_lpg.files import export_file, import_file
from ifc_lpg.merge import (
    MergeConflict,
    MergeError,
    PrepareConflict,
    merge_building,
    merge_report,
    prepare_other,
)
from ifc_lpg.paths import unreachable
from ifc_lpg.prepare import prepare_file
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


DEPTH = (
    "MATCH (:IfcWall {model_id: $model, Name: 'Annex wall'})-[:REPRESENTATION]->()-[:REPRESENTATIONS]->()"
    "-[:ITEMS]->(s:IfcExtrudedAreaSolid) RETURN s.Depth AS depth"
)


async def test_a_building_in_millimetres_is_prepared_first(graph: Any, tmp_path: Path) -> None:
    millimetres = tmp_path / "annex-mm.ifc"
    prepare_file(ANNEX, millimetres, schema=None, length_unit="MILLIMETER")
    await import_file(graph, SPATIAL, "a")
    await import_file(graph, millimetres, "mm")
    assert await graph.run(DEPTH, model="mm") == [{"depth": 2500.0}]
    with pytest.raises(MergeError, match="prepare_other converts"):
        await merge_report(graph, "a", "mm", ANNEX_BUILDING)
    done = await prepare_other(graph, "mm", "mm-ready", base_id="a")
    assert done["length_unit"][1] == "METER" and done["check"] == "passed"
    assert done["model"]["state"] == "done"
    await merge_building(graph, "a", "mm-ready", ANNEX_BUILDING, "c", choices={WALL: "new"})
    assert await graph.run(DEPTH, model="c") == [{"depth": pytest.approx(2.5)}]
    with pytest.raises(MergeError, match="nothing to prepare"):
        await prepare_other(graph, "mm-ready", "again", base_id="a")


async def test_a_building_of_another_schema_is_migrated_first(graph: Any, tmp_path: Path) -> None:
    await import_file(graph, FIXTURES_DIR / "three_walls_gtin.ifc", "old")
    await import_file(graph, ANNEX, "b")
    with pytest.raises(MergeError, match="prepare_other migrates"):
        await merge_report(graph, "old", "b", ANNEX_BUILDING)
    with pytest.raises(PrepareConflict) as raised:
        await prepare_other(graph, "b", "b-ready", base_id="old")
    found = {problem["key"]: problem["options"] for problem in raised.value.report["problems"]}
    assert found == {
        "owner_history": ["owner"],
        "styled_item_styles": ["assignment", "leave_out"],
    }, "IFC4 lets an object have no owner history and a styled item hold its styles; IFC2X3 does not"
    assert await graph.run("MATCH (m:Model {id: 'b-ready'}) RETURN m.id AS id") == [], "nothing before the choice"
    choices = {"owner_history": "owner", "styled_item_styles": "assignment"}
    done = await prepare_other(graph, "b", "b-ready", base_id="old", resolutions=choices)
    assert done["schema"] == ["IFC4", "IFC2X3"] and done["length_unit"][1] == "MILLIMETER"
    assert (done["model"]["prepared_check"], done["model"]["prepared_from"]) == ("passed", "b")
    merged = await merge_building(graph, "old", "b-ready", ANNEX_BUILDING, "c")
    assert merged["model"]["schema"] == "IFC2X3"
    assert await graph.run(DEPTH, model="c") == [{"depth": pytest.approx(2500.0)}]
    owners = await graph.run(
        "MATCH (r:IfcRelAggregates {model_id: 'c'})-[:RELATED_OBJECTS]->(:IfcBuilding {Name: 'Annex'}) "
        "RETURN r.OwnerHistory__step IS NOT NULL AS owned"
    )
    assert owners == [{"owned": True}], "IFC2X3 needs an owner history: the building's, or else the base project's"
    lacking = await graph.run(
        "MATCH (n:Entity {model_id: 'c'}) WHERE n.GlobalId IS NOT NULL AND n.OwnerHistory__step IS NULL "
        "AND NOT n:IfcProject RETURN count(n) AS n"
    )
    assert lacking == [{"n": 0}], "what came from IFC4 without one has the base project's"
    await comes_back(graph, "c", tmp_path)


async def test_an_application_is_one_in_the_merged_model(graph: Any, tmp_path: Path) -> None:
    await import_file(graph, FIXTURES_DIR / "three_walls_gtin.ifc", "old")
    [building] = await names(graph, "MATCH (b:IfcBuilding {model_id: 'old'}) RETURN b.GlobalId AS v")
    report = await merge_report(graph, "old", "old", building)
    choices = {c["global_id"]: "new" for c in report["collisions"]}
    await merge_building(graph, "old", "old", building, "c", choices=choices)
    assert await names(graph, "MATCH (a:IfcApplication {model_id: 'c'}) RETURN count(a) AS v") == [1], (
        "the schema keeps an application's identifier unique, so the copies use A's"
    )
    await comes_back(graph, "c", tmp_path)


async def test_every_object_of_b_in_place_of_a_s(graph: Any, tmp_path: Path) -> None:
    await import_file(graph, SPATIAL, "a")
    report = await merge_report(graph, "a", "a", BUILDING)
    choices = {c["global_id"]: "b" for c in report["collisions"]}
    await merge_building(graph, "a", "a", BUILDING, "c", choices=choices)
    query = "MATCH (n:Entity {model_id: $model}) WHERE n.GlobalId IS NOT NULL RETURN n.GlobalId AS v ORDER BY v"
    assert [r["v"] for r in await graph.run(query, model="c")] == [r["v"] for r in await graph.run(query, model="a")]
    assert await names(
        graph,
        "MATCH (:IfcSite {model_id: 'c'})<-[:RELATING_OBJECT]-()-[:RELATED_OBJECTS]->(b:IfcBuilding) "
        "RETURN b.step_id > 114 AS v",
    ) == [True], "B's building in A's place below the site"
    assert await unreachable(graph, "c") == [], "what A's objects held alone went with them"
    await comes_back(graph, "c", tmp_path)


async def test_what_a_s_replaced_objects_held_alone_goes_with_them(graph: Any) -> None:
    await import_file(graph, FIXTURES_DIR / "three_walls_gtin.ifc", "old")
    [building] = await names(graph, "MATCH (b:IfcBuilding {model_id: 'old'}) RETURN b.GlobalId AS v")
    report = await merge_report(graph, "old", "old", building)
    await merge_building(
        graph, "old", "old", building, "c", choices={c["global_id"]: "b" for c in report["collisions"]}
    )
    before = sorted(r["ifc_class"] for r in await unreachable(graph, "old"))
    assert sorted(r["ifc_class"] for r in await unreachable(graph, "c")) == before, (
        "nothing is left that only A's replaced objects and their relationships held, such as a material"
    )


THREE_WALLS = FIXTURES_DIR / "three_walls_gtin.ifc"
NORWEGIAN_BUILDING = "3AMeTOOFsdVLZuNV0$27s5"


async def test_b_for_one_wall_places_what_a_placed_relative_to_it(graph: Any, tmp_path: Path) -> None:
    renamed = tmp_path / "renamed.ifc"
    renamed.write_bytes(
        THREE_WALLS.read_bytes().replace(
            b"'1Xezbq735AdBO_4DerG66G',#32,'YVT-A'", b"'1Xezbq735AdBO_4DerG66G',#32,'YVT-Z'"
        )
    )
    await import_file(graph, THREE_WALLS, "a")
    await import_file(graph, renamed, "b")
    await merge_building(graph, "a", "b", NORWEGIAN_BUILDING, "c", choices={"1Xezbq735AdBO_4DerG66G": "b"})
    walls = await names(graph, "MATCH (w:IfcWall {model_id: 'c'}) RETURN w.Name AS v ORDER BY v")
    assert "YVT-Z" in walls and "YVT-A" not in walls
    before = sorted(r["ifc_class"] for r in await unreachable(graph, "a"))
    assert sorted(r["ifc_class"] for r in await unreachable(graph, "c")) == before, (
        "no placement is left that places nothing: B's wall is placed in A's storey, and A's old placement went"
    )
    await comes_back(graph, "c", tmp_path)


async def test_a_unit_with_a_factor_of_its_own_is_not_turned(graph: Any, tmp_path: Path) -> None:
    metres = tmp_path / "metres.ifc"
    metres.write_bytes(
        THREE_WALLS.read_bytes().replace(
            b"IFCSIUNIT(*,.LENGTHUNIT.,.MILLI.,.METRE.)", b"IFCSIUNIT(*,.LENGTHUNIT.,$,.METRE.)"
        )
    )
    await import_file(graph, THREE_WALLS, "a")
    await import_file(graph, metres, "b")
    await prepare_other(graph, "b", "b-ready", base_id="a")
    report = await merge_report(graph, "a", "b-ready", NORWEGIAN_BUILDING)
    assert report["collisions"], "the lengths now differ, and the units are the same"


async def test_new_for_one_wall_keeps_what_it_has(graph: Any, tmp_path: Path) -> None:
    renamed = tmp_path / "renamed.ifc"
    renamed.write_bytes(
        THREE_WALLS.read_bytes().replace(
            b"'1Xezbq735AdBO_4DerG66G',#32,'YVT-A'", b"'1Xezbq735AdBO_4DerG66G',#32,'YVT-Z'"
        )
    )
    await import_file(graph, THREE_WALLS, "a")
    await import_file(graph, renamed, "b")
    merged = await merge_building(graph, "a", "b", NORWEGIAN_BUILDING, "c", choices={"1Xezbq735AdBO_4DerG66G": "new"})
    assert {c["choice"] for c in merged["collisions"] if c["same"]} == {"a"}
    [wall] = await graph.run(
        "MATCH (w:IfcWall {model_id: 'c', Name: 'YVT-Z'}) "
        "OPTIONAL MATCH (w)-[:CONTAINED_IN_STRUCTURE]->()-[:RELATING_STRUCTURE]->(s) "
        "OPTIONAL MATCH (w)-[:HAS_ASSOCIATIONS]->(m:IfcRelAssociatesMaterial)-[:RELATED_OBJECTS]->(o) "
        "RETURN w.GlobalId <> '1Xezbq735AdBO_4DerG66G' AS renamed, s.Name AS storey, collect(DISTINCT o.Name) AS shared"
    )
    assert wall["renamed"] and wall["storey"] is not None
    assert sorted(wall["shared"]) == ["YVT-A", "YVT-Z"], (
        "the new wall joins A's material, which it shares with A's wall"
    )
    before = sorted(r["ifc_class"] for r in await unreachable(graph, "a"))
    assert sorted(r["ifc_class"] for r in await unreachable(graph, "c")) == before
    await comes_back(graph, "c", tmp_path)


async def test_units_that_a_has_none_of_come_along(graph: Any, tmp_path: Path) -> None:
    await import_file(graph, SPATIAL, "a")
    await import_file(graph, THREE_WALLS, "old")
    await prepare_other(graph, "old", "old-ready", base_id="a")
    report = await merge_report(graph, "a", "old-ready", NORWEGIAN_BUILDING)
    assert "AREAUNIT SQUARE_METRE" in report["units_added"]
    assert not [unit for unit in report["units_added"] if unit.startswith("LENGTHUNIT")], "A's length stays"
    await merge_building(graph, "a", "old-ready", NORWEGIAN_BUILDING, "c")
    kinds = await names(
        graph,
        "MATCH (:IfcProject {model_id: 'c'})-[:UNITS_IN_CONTEXT]->()-[:UNITS]->(u) "
        "RETURN coalesce(u.UnitType, 'money') AS v ORDER BY v",
    )
    assert len(kinds) == 1 + len(report["units_added"]) == len(set(kinds)), "one unit of each kind"
    await comes_back(graph, "c", tmp_path)


def with_degrees(source: Path, target: Path, name: bytes, factor: bytes) -> Path:
    """A copy whose units have a degree as well, under ``name`` and with ``factor``."""
    data = source.read_bytes().replace(b"=IFCUNITASSIGNMENT((#2));", b"=IFCUNITASSIGNMENT((#2,#200));")
    units = (
        b"#200=IFCCONVERSIONBASEDUNIT(#201,.PLANEANGLEUNIT.,'" + name + b"',#202);\n"
        b"#201=IFCDIMENSIONALEXPONENTS(0,0,0,0,0,0,0);\n"
        b"#202=IFCMEASUREWITHUNIT(IFCPLANEANGLEMEASURE(" + factor + b"),#203);\n"
        b"#203=IFCSIUNIT(*,.PLANEANGLEUNIT.,$,.RADIAN.);\nENDSEC;\nEND-ISO-10303-21;"
    )
    target.write_bytes(data.replace(b"ENDSEC;\nEND-ISO-10303-21;", units))
    return target


async def test_a_unit_is_the_same_by_what_it_means(graph: Any, tmp_path: Path) -> None:
    await import_file(graph, with_degrees(SPATIAL, tmp_path / "a.ifc", b"DEGREE", b"0.0174532925199433"), "a")
    await import_file(graph, with_degrees(ANNEX, tmp_path / "b.ifc", b"degree", b"0.017453292519943295"), "b")
    report = await merge_report(graph, "a", "b", ANNEX_BUILDING)
    assert report["units_added"] == [], "'DEGREE' and 'degree' of the same factor are one unit"
    await import_file(graph, with_degrees(ANNEX, tmp_path / "grad.ifc", b"GRAD", b"0.015707963267949"), "grad")
    with pytest.raises(MergeError, match="units differ"):
        await merge_report(graph, "a", "grad", ANNEX_BUILDING)


async def test_what_ifc4x3_has_not_is_converted_as_the_user_chooses(graph: Any, tmp_path: Path) -> None:
    base = tmp_path / "spatial-4x3.ifc"
    base.write_bytes(SPATIAL.read_bytes().replace(b"FILE_SCHEMA(('IFC4'));", b"FILE_SCHEMA(('IFC4X3_ADD2'));"))
    await import_file(graph, base, "a")
    await import_file(graph, FIXTURES_DIR / "window_style.ifc", "w")
    with pytest.raises(PrepareConflict) as raised:
        await prepare_other(graph, "w", "w-ready", base_id="a")
    found = {problem["key"]: problem["options"] for problem in raised.value.report["problems"]}
    assert found == {
        "IfcWindowStyle": ["type", "proxy", "leave_out"],
        "space_boundary_without_element": ["virtual", "leave_out"],
        "owner_history_dates": ["dated"],
    }
    choices = {"IfcWindowStyle": "type", "space_boundary_without_element": "virtual", "owner_history_dates": "dated"}
    done = await prepare_other(graph, "w", "w-ready", base_id="a", resolutions=choices)
    assert json.loads(done["model"]["prepared_resolutions"]) == choices
    await merge_building(graph, "a", "w-ready", "0Window000000000000003", "c")
    assert await names(graph, "MATCH (t:IfcWindowType {model_id: 'c'}) RETURN t.Name AS v") == ["Casement"]
    await comes_back(graph, "c", tmp_path)
