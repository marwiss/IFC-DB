# This file was generated with the assistance of an AI coding tool.
"""Paths against a real graph: every path leads to one node, and the canonical one leads back."""

from __future__ import annotations

from typing import Any

import pytest

from ifc_lpg.files import import_file
from ifc_lpg.paths import PathError, canonical_path, resolve
from tests.conftest import FIXTURES_DIR

pytestmark = pytest.mark.integration
pytest.importorskip("ifcopenshell", reason="IfcOpenShell is not installed")
pytest.importorskip("neo4j", reason="the neo4j extra is not installed")

WALL = "0Spatial00000000000010"
COLUMN_A, COLUMN_B = "0Spatial00000000000011", "0Spatial00000000000012"


async def test_a_path_leads_forward_and_backward_to_one_node(graph: Any) -> None:
    await import_file(graph, FIXTURES_DIR / "spatial.ifc", "m", include_geometry=True)
    style = await resolve(graph, "m", WALL, "Representation.Representations[0].Items[0].StyledByItem[0].Styles[0]")
    assert style == {"id": "m:39", "step": 39, "ifc_class": "IfcSurfaceStyle"}, "backward through StyledByItem"
    assert (await resolve(graph, "m", WALL, "Representation.Representations.Items.StyledByItem.Styles"))["step"] == 39
    assert (await resolve(graph, "m", WALL, ""))["step"] == 30, "the instance itself"
    opening = await resolve(graph, "m", WALL, "HasOpenings[0].RelatedOpeningElement")
    assert opening["step"] == 61
    door = await resolve(graph, "m", WALL, "HasOpenings[0].RelatedOpeningElement.HasFillings[0].RelatedBuildingElement")
    assert door["ifc_class"] == "IfcDoor"
    assert (await resolve(graph, "m", WALL, "ObjectPlacement.RelativePlacement.Location"))["step"] == 32
    shared = await resolve(graph, "m", COLUMN_B, "Representation.Representations[0].Items[0].MappingSource")
    assert shared["step"] == 45, "reached from either column"


async def test_the_canonical_path_names_a_node_and_leads_back_to_it(graph: Any) -> None:
    await import_file(graph, FIXTURES_DIR / "spatial.ifc", "m", include_geometry=True)
    assert await canonical_path(graph, "m", 30) == {"global_id": WALL, "path": ""}
    colour = await canonical_path(graph, "m", 41)
    assert colour == {
        "global_id": WALL,
        "path": "Representation.Representations.Items.StyledByItem[0].Styles.Styles.SurfaceColour",
    }
    assert (await resolve(graph, "m", colour["global_id"], colour["path"]))["step"] == 41
    shared = await canonical_path(graph, "m", 45)
    assert shared == {"global_id": COLUMN_A, "path": "Representation.Representations.Items.MappingSource"}, (
        "the lowest GlobalId of those that reach it"
    )
    for step in (4, 5, 11, 32, 34, 42, 46, 62, 72, 85, 113):
        named = await canonical_path(graph, "m", step)
        assert (await resolve(graph, "m", named["global_id"], named["path"]))["step"] == step, named


async def test_in_the_skeleton_form_a_path_stops_at_a_block(graph: Any) -> None:
    await import_file(graph, FIXTURES_DIR / "spatial.ifc", "m")
    point = await resolve(graph, "m", WALL, "ObjectPlacement.RelativePlacement.Location")
    assert point == {"step": 32, "in_block": True}, "a point is kept in a block, and is no node"
    with pytest.raises(PathError, match="no path goes on"):
        await resolve(graph, "m", WALL, "ObjectPlacement.RelativePlacement.Location.Coordinates")
    with pytest.raises(PathError, match="has no reference StyledByItem"):
        await resolve(graph, "m", WALL, "Representation.Representations.Items.StyledByItem")
    with pytest.raises(PathError, match="no GlobalId reaches #39"):
        await canonical_path(graph, "m", 39), "only the styled item in a block refers to the style"


async def test_what_leads_to_no_node_is_an_error(graph: Any) -> None:
    await import_file(graph, FIXTURES_DIR / "spatial.ifc", "m")
    with pytest.raises(PathError, match="no instance of the model has the GlobalId"):
        await resolve(graph, "m", "nowhere", "")
    with pytest.raises(PathError, match="has no reference Nowhere"):
        await resolve(graph, "m", WALL, "Representation.Nowhere")
    with pytest.raises(PathError, match=r"has no position \[7\]"):
        await resolve(graph, "m", WALL, "Representation.Representations[7]")
    with pytest.raises(PathError, match=r"has 1 members, no position \[1\]"):
        await resolve(graph, "m", WALL, "HasOpenings[1]")
    with pytest.raises(PathError, match="has no node #999"):
        await canonical_path(graph, "m", 999)
