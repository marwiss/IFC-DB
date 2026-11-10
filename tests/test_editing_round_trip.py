# This file was generated with the assistance of an AI coding tool.
"""Changes in a real graph: what refers to a node, and inverse relationships made anew after a change."""

from __future__ import annotations

from typing import Any

import pytest

from ifc_lpg.editing import referrers, refresh_inverses
from ifc_lpg.files import import_file
from ifc_lpg.paths import resolve
from tests.conftest import FIXTURES_DIR

pytestmark = pytest.mark.integration
pytest.importorskip("ifcopenshell", reason="IfcOpenShell is not installed")
pytest.importorskip("neo4j", reason="the neo4j extra is not installed")

COLUMN_A = "0Spatial00000000000011"
INVERSE = (
    "MATCH (t:Entity {model_id: $model})-[r]->(a:Entity) WHERE r.position IS NULL "
    "RETURN t.step_id AS t, type(r) AS type, a.step_id AS a ORDER BY t, type, a"
)


@pytest.mark.parametrize(
    ("fixture", "options"), [("spatial.ifc", {"include_geometry": True}), ("three_walls_gtin.ifc", {})]
)
async def test_inverses_made_anew_are_those_of_the_import(graph: Any, fixture: str, options: dict[str, Any]) -> None:
    await import_file(graph, FIXTURES_DIR / fixture, "m", **options)
    before = await graph.run(INVERSE, model="m")
    steps = [row["step"] for row in await graph.run("MATCH (n:Entity {model_id: 'm'}) RETURN n.step_id AS step")]
    counts = await refresh_inverses(graph, "m", steps)
    assert counts == {"removed": len(before), "created": len(before)} and len(before) > 0
    assert await graph.run(INVERSE, model="m") == before


async def test_a_changed_reference_moves_its_inverse(graph: Any) -> None:
    await import_file(graph, FIXTURES_DIR / "spatial.ifc", "m")
    await graph.run(
        "MATCH (v:Entity {id: 'm:69'})-[r:RELATING_BUILDING_ELEMENT]->(:Entity {id: 'm:30'}) DELETE r "
        "WITH v MATCH (c:Entity {id: 'm:50'}) CREATE (v)-[:RELATING_BUILDING_ELEMENT {position: 0}]->(c)"
    )
    assert await refresh_inverses(graph, "m", [69]) == {"removed": 2, "created": 2}
    opening = await resolve(graph, "m", COLUMN_A, "HasOpenings[0].RelatedOpeningElement")
    assert opening["step"] == 61, "the column has the opening now"
    rows = await graph.run("MATCH (:Entity {id: 'm:30'})-[r:HAS_OPENINGS]->() RETURN count(r) AS n")
    assert rows == [{"n": 0}], "and the wall has it no longer"


async def test_what_refers_to_a_node_from_the_graph_and_from_a_block(graph: Any) -> None:
    await import_file(graph, FIXTURES_DIR / "spatial.ifc", "m")
    assert await referrers(graph, "m", 31) == [
        {"step": 30, "ifc_class": "IfcWall", "attribute": "ObjectPlacement", "position": 0},
        {"step": 62, "ifc_class": "IfcLocalPlacement", "attribute": "PlacementRelTo", "position": 0},
    ]
    await import_file(graph, FIXTURES_DIR / "styled_mesh.ifc", "mesh")
    assert await referrers(graph, "mesh", 43) == [{"step": 45, "in_block": True}], "a circle in a block"
    assert await referrers(graph, "mesh", 42) == [], "an unused placement"
