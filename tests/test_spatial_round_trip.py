# This file was generated with the assistance of an AI coding tool.
"""The spatial layer in a real graph database: the same Cypher on Neo4j and Memgraph, and the same file back."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ifc_lpg.equivalence import compare_files
from ifc_lpg.files import export_file, import_file
from ifc_lpg.spatial import SpatialError, add_spatial
from tests.conftest import FIXTURES_DIR, Target

pytestmark = pytest.mark.integration
pytest.importorskip("ifcopenshell", reason="IfcOpenShell is not installed")
pytest.importorskip("neo4j", reason="the neo4j extra is not installed")

SPATIAL = FIXTURES_DIR / "spatial.ifc"


async def test_the_boxes_answer_the_same_cypher_on_both_and_the_file_stays_the_same(
    graph: Any, target: Target, tmp_path: Path
) -> None:
    await import_file(graph, SPATIAL, "spatial")
    dialect: Any = target.dialect
    assert await add_spatial(graph, SPATIAL, "spatial", dialect=dialect) == {"products": 7, "meshes": 6, "failed": 1}
    if dialect == "memgraph":  # Neo4j gives its plan in the summary, which graph.run does not return
        plan = await graph.run(
            "EXPLAIN MATCH (n:Entity {model_id: 'spatial'}) "
            "WHERE point.distance(n.bbox_centre, point({x: 3.0, y: 0.0, z: 1.05})) < 1.2 RETURN n.Name"
        )
        assert "ScanAllByPointDistance" in str(plan), "the point index is in use, and made anew after the boxes"
    near = await graph.run(
        "MATCH (d:IfcDoor {model_id: 'spatial'}), (n:Entity {model_id: 'spatial'}) "
        "WHERE n <> d AND point.distance(n.bbox_centre, d.bbox_centre) < 1.2 RETURN n.Name AS name ORDER BY name"
    )
    assert near == [{"name": "Opening"}, {"name": "Wall"}], "what lies within 1.2 m of the door"
    inside = await graph.run(
        "MATCH (n:Entity {model_id: 'spatial'}) "
        "WHERE point.withinBBox(n.bbox_centre, point({x: 5.0, y: -1.0, z: 0.0}), point({x: 9.0, y: 1.0, z: 3.0})) "
        "RETURN n.Name AS name, n.bbox_centre.srid AS srid ORDER BY name"
    )
    assert inside == [{"name": "Column A", "srid": 9157}, {"name": "Column B", "srid": 9157}]
    overlap = await graph.run(
        "MATCH (d:IfcDoor {model_id: 'spatial'}), (n:Entity {model_id: 'spatial'}) WHERE n <> d "
        "AND n.bbox_min.x <= d.bbox_max.x AND d.bbox_min.x <= n.bbox_max.x "
        "AND n.bbox_min.y <= d.bbox_max.y AND d.bbox_min.y <= n.bbox_max.y "
        "AND n.bbox_min.z <= d.bbox_max.z AND d.bbox_min.z <= n.bbox_max.z RETURN n.Name AS name ORDER BY name"
    )
    assert overlap == [{"name": "Opening"}, {"name": "Wall"}], "the boxes that overlap the door's"
    [model] = await graph.run(
        "MATCH (m:Model {id: 'spatial'}) RETURN m.spatial_products AS products, m.spatial_failed AS failed, "
        "m.spatial_at IS NOT NULL AS at"
    )
    assert model == {"products": 7, "failed": 1, "at": True}

    back = tmp_path / "back.ifc"
    await export_file(graph, "spatial", back)
    assert compare_files(SPATIAL, back).identical, "the writer reads only the schema's attributes"
    assert await add_spatial(graph, SPATIAL, "spatial", dialect=dialect) == {"products": 7, "meshes": 6, "failed": 1}
    counted = await graph.run(
        "MATCH (n:Entity {model_id: 'spatial'}) WHERE n.bbox_centre IS NOT NULL RETURN count(n) AS n"
    )
    assert counted == [{"n": 7}], "worked out again, in place of the boxes from before"


async def test_boxes_from_another_file_change_nothing(graph: Any) -> None:
    await import_file(graph, FIXTURES_DIR / "three_walls_gtin.ifc", "walls")
    with pytest.raises(SpatialError, match="7 of the 7 products are not in the model"):
        await add_spatial(graph, SPATIAL, "walls")
    rows = await graph.run("MATCH (n:Entity {model_id: 'walls'}) WHERE n.bbox_centre IS NOT NULL RETURN count(n) AS n")
    assert rows == [{"n": 0}]
    with pytest.raises(SpatialError, match="no such model"):
        await add_spatial(graph, SPATIAL, "nowhere")
