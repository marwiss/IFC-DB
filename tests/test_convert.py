# This file was generated with the assistance of an AI coding tool.
"""The IFC converter on the fixture model (skipped when IfcOpenShell is not installed)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ifc_db.worker import main as worker_main
from tests.conftest import FIXTURES_DIR

pytest.importorskip("ifcopenshell", reason="the ifc-db extra is not installed")

from ifc_db.convert import convert

MODEL = FIXTURES_DIR / "three_walls_gtin.ifc"
WALLS = ("1Xezbq735AdBO_4DerG66G", "3eT96F_cnDsOufCEsotwtp", "3k3LbXmTfDWODxdSuyCGYE")


def test_convert_produces_labelled_nodes_and_attribute_edges() -> None:
    records = list(convert(MODEL))
    header = next(r for r in records if r["t"] == "h")
    nodes = [r for r in records if r["t"] == "n"]
    edges = [r for r in records if r["t"] == "e"]
    assert header["schema"] == "IFC2X3" and header["ifc_project_guid"] == "1CGyzxdzHaJybf9L4wHy3R"
    assert header["entities"] == len(nodes) and header["relationships"] == len(edges)
    assert header["skipped"] > 0, "geometry is left out by default"
    assert not any("IfcCartesianPoint" in n["labels"] for n in nodes)

    # IfcOpenShell goes through a file in an order that differs between platforms.
    walls = sorted((n for n in nodes if n["props"].get("GlobalId") in WALLS), key=lambda n: n["props"]["Name"])
    assert len(walls) == 3
    labels = walls[0]["labels"]
    assert (
        labels[0] == "IfcWallStandardCase"
        and "IfcWall" in labels
        and "IfcProduct" in labels
        and labels[-1] == "IfcRoot"
    )
    assert walls[0]["props"]["ifc_class"] == "IfcWallStandardCase" and walls[0]["props"]["Name"] == "YVT-A"
    assert "OwnerHistory" not in walls[0]["props"]

    by_step = {n["step"]: n for n in nodes}
    gtin_values = {
        n["props"]["NominalValue"]
        for n in nodes
        if n["props"].get("ifc_class") == "IfcPropertySingleValue" and n["props"].get("Name") == "GTIN"
    }
    assert gtin_values == {"1", "2", "3"}, "select values are unwrapped to their literal"
    wall_step = walls[0]["step"]
    defined_by = [e for e in edges if e["a"] == wall_step and e["type"] == "IsDefinedBy"]
    assert defined_by, "inverse attributes become edges too"
    relating = [e for e in edges if e["a"] == defined_by[0]["b"] and e["type"] == "RelatingPropertyDefinition"]
    assert relating and by_step[relating[0]["b"]]["props"]["ifc_class"] == "IfcPropertySet"
    assert all(e["a"] in by_step and e["b"] in by_step for e in edges), "edges only between exported nodes"
    owner_histories = [n for n in nodes if n["props"]["ifc_class"] == "IfcOwnerHistory"]
    assert len(owner_histories) == 1, "only the project's owner history is kept"
    linked_from = [by_step[e["a"]]["props"]["ifc_class"] for e in edges if e["b"] == owner_histories[0]["step"]]
    assert linked_from == ["IfcProject"], "and every other entity that shares it has no edge to it"


def test_geometry_can_be_included_and_limits_are_enforced(tmp_path: Path) -> None:
    with_geometry = [r for r in convert(MODEL, include_geometry=True) if r["t"] == "n"]
    assert any("IfcCartesianPoint" in n["labels"] for n in with_geometry)
    with pytest.raises(Exception, match="more than 10 entities"):
        list(convert(MODEL, max_entities=10))

    out = tmp_path / "records.jsonl"
    assert worker_main([str(MODEL), str(out)]) == 0
    kinds = [json.loads(line)["t"] for line in out.read_text().splitlines()]
    assert kinds.index("h") > 0 and all(k == "e" for k in kinds[kinds.index("h") + 1 :]), "nodes, header, then edges"
    assert worker_main([str(MODEL), str(out), "--max-entities", "10"]) == 3
    assert worker_main([str(tmp_path / "missing.ifc"), str(out)]) == 4


def _heights(data: str) -> dict[int, dict[str, object]]:
    from ifc_db.convert import storey_heights
    from ifc_db.schema import Schema
    from ifc_db.step import StepFile

    text = (
        "ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION((''),'2;1');\nFILE_NAME('','',(''),(''),'','','');\n"
        f"FILE_SCHEMA(('IFC4'));\nENDSEC;\nDATA;\n{data}\nENDSEC;\nEND-ISO-10303-21;\n"
    )
    file = StepFile(text.encode("ascii"))
    return storey_heights(file, Schema(file.schema))


_PROJECT = """#1=IFCPROJECT('0Height000000000000001',$,'P',$,$,$,$,$,#3);
#3=IFCUNITASSIGNMENT((#2));"""


def test_a_storey_without_elevation_takes_the_height_of_its_placement() -> None:
    found = _heights(
        _PROJECT
        + """
#2=IFCSIUNIT(*,.LENGTHUNIT.,.MILLI.,.METRE.);
#10=IFCCARTESIANPOINT((0.,0.,10000.));
#11=IFCAXIS2PLACEMENT3D(#10,$,$);
#12=IFCLOCALPLACEMENT($,#11);
#20=IFCDIRECTION((0.,1.,0.));
#21=IFCDIRECTION((1.,0.,0.));
#22=IFCCARTESIANPOINT((0.,0.,500.));
#23=IFCAXIS2PLACEMENT3D(#22,#20,#21);
#24=IFCLOCALPLACEMENT(#12,#23);
#30=IFCCARTESIANPOINT((0.,2000.,0.));
#31=IFCAXIS2PLACEMENT3D(#30,$,$);
#32=IFCLOCALPLACEMENT(#24,#31);
#40=IFCBUILDINGSTOREY('0Height000000000000002',$,'Up',$,$,#12,$,$,.ELEMENT.,$);
#41=IFCBUILDINGSTOREY('0Height000000000000003',$,'Turned',$,$,#32,$,$,.ELEMENT.,$);
#42=IFCBUILDINGSTOREY('0Height000000000000004',$,'Given',$,$,#32,$,$,.ELEMENT.,2500.);
#43=IFCBUILDINGSTOREY('0Height000000000000005',$,'Nowhere',$,$,$,$,$,.ELEMENT.,$);"""
    )
    assert found[40] == {"elevation_m": 10.0, "elevation_source": "placement"}
    # the middle placement turns its z axis to the world's y, so the storey's 2 m along y go 2 m down
    assert found[41]["elevation_source"] == "placement"
    assert found[41]["elevation_m"] == pytest.approx(10.0 + 0.5 - 2.0)
    assert found[42] == {"elevation_m": 2.5, "elevation_source": "attribute"}, "the file's own elevation goes first"
    assert 43 not in found, "a storey with no placement and no elevation has no height"


def test_a_length_unit_converted_from_the_metre_scales_the_height() -> None:
    found = _heights(
        _PROJECT
        + """
#2=IFCCONVERSIONBASEDUNIT(#4,.LENGTHUNIT.,'FOOT',#5);
#4=IFCDIMENSIONALEXPONENTS(1,0,0,0,0,0,0);
#5=IFCMEASUREWITHUNIT(IFCLENGTHMEASURE(0.3048),#6);
#6=IFCSIUNIT(*,.LENGTHUNIT.,$,.METRE.);
#40=IFCBUILDINGSTOREY('0Height000000000000002',$,'Ten feet',$,$,$,$,$,.ELEMENT.,10.);"""
    )
    assert found[40]["elevation_m"] == pytest.approx(3.048)


def test_no_height_is_given_when_the_length_unit_is_not_known() -> None:
    assert _heights("#40=IFCBUILDINGSTOREY('0Height000000000000002',$,'S',$,$,$,$,$,.ELEMENT.,3.);") == {}
