# This file was generated with the assistance of an AI coding tool.
"""The named queries asked of real graphs: the walls, their storey, materials and a property, and the spaces."""

from __future__ import annotations

from typing import Any

import pytest

from ifc_lpg.files import import_file
from ifc_lpg.queries import QUERIES, property_values, run_query
from tests.conftest import FIXTURES_DIR

pytestmark = pytest.mark.integration
pytest.importorskip("ifcopenshell", reason="IfcOpenShell is not installed")
pytest.importorskip("neo4j", reason="the neo4j extra is not installed")

WALLS = {"1Xezbq735AdBO_4DerG66G", "3eT96F_cnDsOufCEsotwtp", "3k3LbXmTfDWODxdSuyCGYE"}
STOREY = "0St1hMhFUHZyrAECcby7I2"


async def ask(graph: Any, model_id: str, name: str, **parameters: str) -> list[dict[str, Any]]:
    answer = await run_query(graph, model_id, name, parameters)
    assert answer["name"] == name and answer["columns"] == list(QUERIES[name].columns) and not answer["truncated"]
    return [dict(zip(answer["columns"], row, strict=True)) for row in answer["rows"]]


async def test_the_walls_their_classes_storey_materials_and_a_property(graph: Any) -> None:
    await import_file(graph, FIXTURES_DIR / "three_walls_gtin.ifc", "walls")

    classes = {row["class"]: row["count"] for row in await ask(graph, "walls", "element-classes")}
    assert classes["IfcWallStandardCase"] == 3
    elements = await ask(graph, "walls", "elements", **{"class": "IfcWall"})
    assert {row["global_id"] for row in elements} == WALLS, "a class takes its subclasses with it"
    assert {row["container"] for row in elements} == {"1. etasje"}
    assert [row["name"] for row in elements] == ["YVT-A", "YVT-B", "YVT-C"]
    (storey,) = await ask(graph, "walls", "storeys")
    assert storey["name"] == "1. etasje" and storey["elevation"] == 1000.0
    assert storey["elements"] >= 3 and storey["spaces"] == 0
    materials = await ask(graph, "walls", "materials", **{"class": "IfcWall"})
    assert len(materials) == 3 and all(row["materials"] == ["Yttervegg"] for row in materials)
    gtin = await ask(graph, "walls", "property", pset="Reuse", property="GTIN")
    assert sorted(row["value"] for row in gtin) == ["1", "2", "3"] and {row["type"] for row in gtin} == {"IFCLABEL"}
    assert await ask(graph, "walls", "property", pset="Reuse", property="GTIN", **{"class": "IfcDoor"}) == []
    assert await ask(graph, "walls", "spaces") == []

    (wall,) = await ask(graph, "walls", "entity", global_id="1Xezbq735AdBO_4DerG66G")
    assert wall["class"] == "IfcWallStandardCase" and wall["step_id"] == 242 and wall["attributes"]["Name"] == "YVT-A"
    assert {reference["attribute"] for reference in wall["references"]} >= {"OBJECT_PLACEMENT", "REPRESENTATION"}
    back = {reference["attribute"]: reference for reference in wall["referenced_by"]}
    assert back["RELATED_ELEMENTS"]["class"] == "IfcRelContainedInSpatialStructure"
    assert back["RELATED_ELEMENTS"]["global_id"] == "0PUNS2OuLwupG5o8M6iDor"
    assert await ask(graph, "walls", "entity", global_id="0000000000000000000000") == []

    some = await run_query(graph, "walls", "elements", {"class": "IfcWall"}, max_rows=2)
    assert len(some["rows"]) == 2 and some["truncated"]
    assert (await run_query(graph, "no-such-model", "element-classes"))["rows"] == []


async def test_spaces_and_the_area_of_their_storey(graph: Any) -> None:
    await import_file(graph, FIXTURES_DIR / "two_spaces.ifc", "spaces")
    spaces = await ask(graph, "spaces", "spaces")
    assert [(row["name"], row["long_name"], row["storey"]) for row in spaces] == [
        ("101", "Kitchen", "Plan 1"),
        ("102", "Living room", "Plan 1"),
    ]
    assert [(row["net_floor_area"], row["gross_floor_area"]) for row in spaces] == [(12.5, 14.0), (20.0, None)]
    (storey,) = await ask(graph, "spaces", "areas")
    assert (storey["storey"], storey["spaces"], storey["net_floor_area"]) == ("Plan 1", 2, 32.5)
    (plan,) = await ask(graph, "spaces", "storeys")
    assert (plan["name"], plan["elements"], plan["spaces"]) == ("Plan 1", 0, 2)


async def test_a_property_for_a_list_of_global_ids(graph: Any) -> None:
    await import_file(graph, FIXTURES_DIR / "three_walls_gtin.ifc", "walls")
    await import_file(graph, FIXTURES_DIR / "two_spaces.ifc", "spaces")
    asked = [*WALLS, STOREY, "0000000000000000000000"]
    found = await property_values(graph, "walls", pset="Reuse", property="GTIN", global_ids=asked)
    assert [(row["global_id"], row["value"], row["type"]) for row in found] == [
        (STOREY, None, None),
        ("1Xezbq735AdBO_4DerG66G", "3", "IFCLABEL"),
        ("3eT96F_cnDsOufCEsotwtp", "2", "IFCLABEL"),
        ("3k3LbXmTfDWODxdSuyCGYE", "1", "IFCLABEL"),
    ], "one row for each the model has, and none for the one it does not"
    assert await property_values(graph, "spaces", pset="Reuse", property="GTIN", global_ids=asked) == []


async def test_where_a_model_stands(graph: Any) -> None:
    await import_file(graph, FIXTURES_DIR / "site.ifc", "site")
    (site,) = await ask(graph, "site", "site")
    assert (site["global_id"], site["name"]) == ("0Site00000000000000002", "Plot")
    assert site["ref_latitude"] == [59, 19, 46, 500000] and site["ref_longitude"] == [18, 4, 7, 0]
    assert site["ref_elevation"] == 25.5
    assert site["latitude"] == pytest.approx(59 + 19 / 60 + 46 / 3600 + 500000 / 3.6e9, abs=1e-9)
    assert site["longitude"] == pytest.approx(18 + 4 / 60 + 7 / 3600, abs=1e-9)
    assert (site["crs"], site["vertical_datum"]) == ("EPSG:3006", "RH2000")
    assert (site["eastings"], site["northings"], site["orthogonal_height"]) == (674032.25, 6580821.5, 25.5)
    assert (site["x_axis_abscissa"], site["x_axis_ordinate"], site["scale"]) == (1.0, 0.0, 1.0)

    await import_file(graph, FIXTURES_DIR / "three_walls_gtin.ifc", "walls")
    (older,) = await ask(graph, "walls", "site")
    assert older["latitude"] == pytest.approx(59 + 55 / 60 + 52 / 3600 + 428000 / 3.6e9, abs=1e-9)
    assert older["longitude"] == pytest.approx(10 + 42 / 60 + 27 / 3600 + 972000 / 3.6e9, abs=1e-9)
    assert older["crs"] is None and older["eastings"] is None, "a model with no map projection has none"

    await import_file(graph, FIXTURES_DIR / "two_spaces.ifc", "spaces")
    (bare,) = await ask(graph, "spaces", "site")
    assert bare["ref_latitude"] is None and bare["latitude"] is None and bare["longitude"] is None
