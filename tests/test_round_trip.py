# This file was generated with the assistance of an AI coding tool.
"""The round trip through a real graph database: a file in, the same file out, and a change in the graph reaches it."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ifc_lpg.equivalence import compare_files
from ifc_lpg.exporter import NotExportable
from ifc_lpg.files import export_file, import_file
from ifc_lpg.step import StepFile
from tests.conftest import FIXTURES_DIR

pytestmark = pytest.mark.integration
pytest.importorskip("ifcopenshell", reason="IfcOpenShell is not installed")
pytest.importorskip("neo4j", reason="the neo4j extra is not installed")

MODEL = FIXTURES_DIR / "three_walls_gtin.ifc"
WALL = "1Xezbq735AdBO_4DerG66G"


async def test_a_model_comes_back_identical(graph: Any, tmp_path: Path) -> None:
    model = await import_file(graph, MODEL, "walls")
    assert model["state"] == "done" and model["lossless"] is True and model["entities"] > 0
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


async def test_a_model_is_imported_once_and_a_failure_leaves_nothing_behind(graph: Any, tmp_path: Path) -> None:
    await import_file(graph, MODEL, "walls")
    with pytest.raises(ValueError, match="exists already"):
        await import_file(graph, MODEL, "walls")
    broken = tmp_path / "broken.ifc"
    broken.write_bytes(MODEL.read_bytes()[:2000])
    with pytest.raises(RuntimeError):
        await import_file(graph, broken, "broken")
    rows = await graph.run("MATCH (m:IfcModel {id: 'broken'}) RETURN m.state AS state, m.error AS error")
    assert rows[0]["state"] == "failed" and rows[0]["error"]
    left = await graph.run("MATCH (n:IfcEntity {model_id: 'broken'}) RETURN count(n) AS n")
    assert left[0]["n"] == 0
    with pytest.raises(NotExportable, match="failed"):
        await export_file(graph, "broken", tmp_path / "never.ifc")
