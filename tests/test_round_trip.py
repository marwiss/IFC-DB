# This file was generated with the assistance of an AI coding tool.
"""The round trip through a real graph database: a file in, the same file out, and a change in the graph reaches it."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ifc_lpg.equivalence import compare_files
from ifc_lpg.exporter import NotExportable
from ifc_lpg.files import convert_file, export_file, import_file
from ifc_lpg.importer import MODEL_FACTS, delete_model, import_records, model_facts
from ifc_lpg.step import StepFile
from tests.conftest import FIXTURES_DIR

pytestmark = pytest.mark.integration
pytest.importorskip("ifcopenshell", reason="IfcOpenShell is not installed")
pytest.importorskip("neo4j", reason="the neo4j extra is not installed")

MODEL = FIXTURES_DIR / "three_walls_gtin.ifc"
WALL = "1Xezbq735AdBO_4DerG66G"


FORMS = {"skeleton": {}, "nodes": {"include_geometry": True}, "blocks": {"geometry_in_blocks": True}}


@pytest.mark.parametrize("form", list(FORMS))
async def test_a_model_comes_back_identical(graph: Any, form: str, tmp_path: Path) -> None:
    model = await import_file(graph, MODEL, "walls", **FORMS[form])
    assert model["state"] == "done" and model["lossless"] is True and model["entities"] > 0
    assert model["geometry"] == form
    target = tmp_path / "back.ifc"
    written = await export_file(graph, "walls", target)
    assert written == len(StepFile(MODEL.read_bytes())), "every instance, the nodes' and the blocks'"
    comparison = compare_files(MODEL, target)
    assert comparison.identical, comparison.to_dict() if hasattr(comparison, "to_dict") else comparison


async def test_every_instance_keeps_its_step_id_and_the_graph_answers_queries(graph: Any) -> None:
    await import_file(graph, MODEL, "walls")
    rows = await graph.run(
        "MATCH (w:IfcWall {model_id: 'walls', GlobalId: $id}) RETURN w.step_id AS step, w.id AS id", id=WALL
    )
    assert len(rows) == 1 and rows[0]["id"] == f"walls:{rows[0]['step']}"
    psets = await graph.run(
        "MATCH (w:IfcWall {GlobalId: $id})-[:IS_DEFINED_BY]->(:IfcRelDefinesByProperties)"
        "-[:RELATING_PROPERTY_DEFINITION]->(p:IfcPropertySet) RETURN p.Name AS name",
        id=WALL,
    )
    assert {row["name"] for row in psets} >= {"Reuse"}


async def test_a_change_in_the_graph_shows_up_in_the_file(graph: Any, tmp_path: Path) -> None:
    await import_file(graph, MODEL, "walls")
    await graph.run("MATCH (w:IfcWall {model_id: 'walls', GlobalId: $id}) SET w.Name = 'Renamed in the graph'", id=WALL)
    target = tmp_path / "changed.ifc"
    await export_file(graph, "walls", target)
    assert b"'Renamed in the graph'" in target.read_bytes()
    assert not compare_files(MODEL, target).same_model, "the written file holds the changed model"


async def test_a_date_a_program_puts_on_a_node_stays_out_of_the_file(graph: Any, tmp_path: Path) -> None:
    await import_file(graph, MODEL, "walls")
    await graph.run("MATCH (w:IfcWall {model_id: 'walls', GlobalId: $id}) SET w.checked_at = datetime()", id=WALL)
    target = tmp_path / "back.ifc"
    await export_file(graph, "walls", target)
    assert compare_files(MODEL, target).identical


async def test_a_list_of_coordinates_is_a_list_of_points_the_database_can_query(graph: Any, tmp_path: Path) -> None:
    source = tmp_path / "points.ifc"
    source.write_bytes(
        b"ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('ViewDefinition [x]'),'2;1');\n"
        b"FILE_NAME('p.ifc','2026-11-10T00:00:00',(''),(''),'','',$);\nFILE_SCHEMA(('IFC4'));\nENDSEC;\nDATA;\n"
        b"#1=IFCCARTESIANPOINTLIST3D(((0.,-0.,5.E-324),(1.7976931348623157E+308,-2.5,1.E-07),(1.,2.,3.)));\n"
        b"#2=IFCCARTESIANPOINTLIST2D(((0.,1.),(2.,3.)));\n"
        b"#3=IFCTRIANGULATEDFACESET(#1,$,$,((1,2,3)),$);\nENDSEC;\nEND-ISO-10303-21;\n"
    )
    await import_file(graph, source, "points", include_geometry=True)
    rows = await graph.run(
        "MATCH (n:IfcCartesianPointList {model_id: 'points'}) "
        "RETURN n.step_id AS step, n.CoordList[1].x AS x, n.CoordList[0].srid AS srid ORDER BY step"
    )
    assert [(row["step"], row["x"], row["srid"]) for row in rows] == [(1, 1.7976931348623157e308, 9157), (2, 2.0, 7203)]
    text = await graph.run("MATCH (n:IfcTriangulatedFaceSet {model_id: 'points'}) RETURN n.CoordIndex AS v")
    assert text[0]["v"] == "[[1, 2, 3]]", "what is no list of points stays text"
    near = await graph.run(
        "MATCH (n:IfcCartesianPointList3D {model_id: 'points'}) "
        "WHERE any(p IN n.CoordList WHERE point.distance(p, point({x: 1.0, y: 2.0, z: 3.5})) < 1.0) "
        "RETURN n.step_id AS step"
    )
    assert [row["step"] for row in near] == [1]
    target = tmp_path / "back.ifc"
    await export_file(graph, "points", target)
    assert compare_files(source, target).identical


async def test_a_model_is_imported_once_and_a_failure_leaves_nothing_behind(graph: Any, tmp_path: Path) -> None:
    await import_file(graph, MODEL, "walls")
    with pytest.raises(ValueError, match="exists already"):
        await import_file(graph, MODEL, "walls")
    broken = tmp_path / "broken.ifc"
    broken.write_bytes(MODEL.read_bytes()[:2000])
    with pytest.raises(RuntimeError):
        await import_file(graph, broken, "broken")
    rows = await graph.run("MATCH (m:Model {id: 'broken'}) RETURN m.state AS state, m.error AS error")
    assert rows[0]["state"] == "failed" and rows[0]["error"]
    left = await graph.run("MATCH (n:Entity {model_id: 'broken'}) RETURN count(n) AS n")
    assert left[0]["n"] == 0
    with pytest.raises(NotExportable, match="failed"):
        await export_file(graph, "broken", tmp_path / "never.ifc")


async def test_the_import_keeps_the_models_record_itself(graph: Any, tmp_path: Path) -> None:
    """Records loaded by a program of its own, with no ``Model`` made first and no state set on it."""
    records, back = tmp_path / "records.jsonl", tmp_path / "back.ifc"
    header = await convert_file(MODEL, records)
    facts = await import_records(graph, "own", records)
    assert facts["schema"] == header["schema"] == "IFC2X3" and facts["lossless"] is True and facts["imported_at"]
    found = await model_facts(graph, "own")
    assert found is not None and found["whole"] and found["id"] == "own"
    assert {key: found[key] for key in MODEL_FACTS} == {key: facts[key] for key in MODEL_FACTS}
    await graph.run("MATCH (m:Model {id: 'own'}) SET m.project_id = 'p', m.state = 'queued'")
    found = await model_facts(graph, "own")
    assert found is not None and found["whole"] and "project_id" not in found and "state" not in found
    await export_file(graph, "own", back)
    assert compare_files(MODEL, back).identical, "whole by its own mark, whatever state another has put there"

    await delete_model(graph, "own", keep_model=True)
    emptied = await model_facts(graph, "own")
    assert emptied is not None and not emptied["whole"], "a model that is taken out is not all there"
    broken = tmp_path / "broken.jsonl"
    broken.write_text(records.read_text(encoding="utf-8")[:4000] + "\n{no json\n", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        await import_records(graph, "own", broken)
    found = await model_facts(graph, "own")
    assert found is not None and not found["whole"] and "imported_at" not in found, "an import that began anew"
    with pytest.raises(NotExportable, match="queued"):
        await export_file(graph, "own", back)
    assert await model_facts(graph, "no-such-model") is None


async def test_a_model_from_before_the_mark_is_whole_when_it_is_done(graph: Any, tmp_path: Path) -> None:
    await import_file(graph, MODEL, "old")
    await graph.run("MATCH (m:Model {id: 'old'}) REMOVE m.imported_at")
    await export_file(graph, "old", tmp_path / "old.ifc")
    assert compare_files(MODEL, tmp_path / "old.ifc").identical
    await graph.run("MATCH (m:Model {id: 'old'}) SET m.state = 'running'")
    with pytest.raises(NotExportable, match="running"):
        await export_file(graph, "old", tmp_path / "never.ifc")
