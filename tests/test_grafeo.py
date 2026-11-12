# This file was generated with the assistance of an AI coding tool.
"""Grafeo's backend: every fixture in, the same file out, and the records Neo4j gives, in each of the three forms.

The core loads and exports through the backend, so what is tried here is the backend's GQL over Grafeo's HTTP API.
Each load is counted after, for Grafeo has taken statements it did not carry out without a word.
``IFC_DB_TEST_GRAFEO=1`` starts it.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest

from ifc_db.backends.grafeo import ConstraintViolation, GrafeoBackend
from ifc_db.equivalence import compare_files
from ifc_db.exporter import NotExportable, export_records
from ifc_db.files import convert_file, write_file
from ifc_db.importer import MODEL_FACTS, delete_model, import_records, model_facts, models
from ifc_db.records import NewEntity
from ifc_db.step import StepFile
from tests.conftest import FIXTURES_DIR
from tests.test_snapshots import FIXTURES, FORMS, MODEL, SNAPSHOTS, _record

pytestmark = pytest.mark.integration
pytest.importorskip("ifcopenshell", reason="IfcOpenShell is not installed")

WALLS = FIXTURES_DIR / "three_walls_gtin.ifc"
EMPTY = {"entities": 0, "relationships": 0, "blocks": 0, "in_blocks": 0}


def _held(records: Path, loaded: dict[str, Any]) -> dict[str, int]:
    """What the database should hold after a load of ``records``, by the records themselves."""
    lines = records.read_text(encoding="utf-8").splitlines()
    blocks = [record for record in map(json.loads, lines) if record["t"] == "g"]
    held = {"entities": loaded["nodes"], "relationships": loaded["relationships"], "blocks": len(blocks)}
    return {**held, "in_blocks": sum(block["instances"] for block in blocks)}


@pytest.mark.parametrize("form", list(FORMS))
@pytest.mark.parametrize("fixture", FIXTURES)
async def test_a_file_comes_back_identical_with_the_records_neo4j_gives(
    grafeo: Any, fixture: str, form: str, tmp_path: Path
) -> None:
    source = FIXTURES_DIR / f"{fixture}.ifc"
    records, out, target = tmp_path / "records.jsonl", tmp_path / "out.jsonl", tmp_path / "back.ifc"
    await convert_file(source, records, **FORMS[form])
    loaded = await import_records(grafeo, MODEL, records)
    assert await grafeo.counts(MODEL) == _held(records, loaded)
    written = await export_records(grafeo, MODEL, out)
    assert written == len(StepFile(source.read_bytes())), "every instance, the nodes' and the blocks'"
    snapshot = json.loads((SNAPSHOTS / f"{fixture}-{form}.json").read_text(encoding="utf-8"))
    assert [_record(line) for line in out.read_text(encoding="utf-8").splitlines()] == snapshot["records"]
    await write_file(out, target)
    comparison = compare_files(source, target)
    assert comparison.identical, comparison


async def test_rows_of_points_are_lists_and_every_number_comes_back(grafeo: Any, tmp_path: Path) -> None:
    source, records = tmp_path / "points.ifc", tmp_path / "records.jsonl"
    source.write_bytes(
        b"ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('ViewDefinition [x]'),'2;1');\n"
        b"FILE_NAME('p.ifc','2026-11-10T00:00:00',(''),(''),'','',$);\nFILE_SCHEMA(('IFC4'));\nENDSEC;\nDATA;\n"
        b"#1=IFCCARTESIANPOINTLIST3D(((0.,-0.,5.E-324),(1.7976931348623157E+308,-2.5,1.E-07),(1.,2.,3.)));\n"
        b"#2=IFCCARTESIANPOINTLIST2D(((0.,1.),(2.,3.)));\n"
        b"#3=IFCTRIANGULATEDFACESET(#1,$,$,((1,2,3)),$);\nENDSEC;\nEND-ISO-10303-21;\n"
    )
    await convert_file(source, records, include_geometry=True)
    loaded = await import_records(grafeo, "points", records)
    assert await grafeo.counts("points") == _held(records, loaded)
    rows = await grafeo.run(
        "MATCH (n:IfcCartesianPointList {model_id: $m}) RETURN n.step_id AS step, n.CoordList AS rows ORDER BY step",
        m="points",
    )
    assert [row["rows"] for row in rows] == [
        [[0.0, -0.0, 5e-324], [1.7976931348623157e308, -2.5, 1e-07], [1.0, 2.0, 3.0]],
        [[0.0, 1.0], [2.0, 3.0]],
    ]
    assert math.copysign(1.0, rows[0]["rows"][0][1]) == -1.0, "the sign of a zero"
    out, target = tmp_path / "out.jsonl", tmp_path / "back.ifc"
    await export_records(grafeo, "points", out)
    await write_file(out, target)
    assert compare_files(source, target).identical


async def test_the_models_record_and_taking_a_model_out(grafeo: Any, tmp_path: Path) -> None:
    records = tmp_path / "records.jsonl"
    await convert_file(WALLS, records)
    facts = await import_records(grafeo, "own", records)
    found = await model_facts(grafeo, "own")
    assert found is not None and found["whole"] and found["id"] == "own"
    assert {key: found[key] for key in MODEL_FACTS} == {key: facts[key] for key in MODEL_FACTS}
    await grafeo.set_facts("own", {"project_id": "p", "spatial_note": "x"})
    await grafeo.set_facts("own", {"spatial_note": None})
    record = await grafeo.model("own")
    assert record is not None and record["project_id"] == "p" and "spatial_note" not in record, "None takes a fact away"
    await import_records(grafeo, "other", records)
    assert await models(grafeo) == [{"id": "other", "whole": True}, {"id": "own", "whole": True}]
    held = await grafeo.counts("other")
    assert held["blocks"] > 0 and held == await grafeo.counts("own")

    assert await delete_model(grafeo, "own", keep_model=True) == facts["entities"]
    emptied = await model_facts(grafeo, "own")
    assert emptied is not None and not emptied["whole"] and "imported_at" not in emptied
    assert await grafeo.counts("own") == EMPTY
    with pytest.raises(NotExportable, match="not done"):
        await export_records(grafeo, "own", tmp_path / "never.jsonl")
    await delete_model(grafeo, "own")
    assert await model_facts(grafeo, "own") is None
    assert await models(grafeo) == [{"id": "other", "whole": True}]
    assert await grafeo.counts("other") == held, "the other model is as it was"
    out, target = tmp_path / "out.jsonl", tmp_path / "back.ifc"
    await export_records(grafeo, "other", out)
    await write_file(out, target)
    assert compare_files(WALLS, target).identical


async def test_the_schema_is_made_twice_and_holds_an_id_once(grafeo: Any) -> None:
    await grafeo.ensure_schema()
    await grafeo.add_entities("m", [NewEntity(1, ("IfcWall",), {"Name": "one"}, {})])
    with pytest.raises(ConstraintViolation, match="UNIQUE constraint violation") as refused:
        await grafeo.add_entities("m", [NewEntity(1, ("IfcSlab",), {"Name": "two"}, {})])
    assert refused.value.status == 400 and refused.value.code == "GRAFEO-V001"
    assert (await grafeo.counts("m"))["entities"] == 1


async def test_a_small_request_limit_sends_rows_and_texts_in_parts(
    grafeo: Any, grafeo_url: str, tmp_path: Path
) -> None:
    records, out, target = tmp_path / "records.jsonl", tmp_path / "out.jsonl", tmp_path / "back.ifc"
    await convert_file(WALLS, records)
    texts = [
        record["text"]
        for record in map(json.loads, records.read_text(encoding="utf-8").splitlines())
        if record["t"] == "g"
    ]
    assert max(map(len, texts)) > 1024 // 2, "a block's text goes in pieces"
    async with GrafeoBackend(grafeo_url, max_body=1024) as small:
        loaded = await import_records(small, MODEL, records)
        assert await small.counts(MODEL) == _held(records, loaded)
        await export_records(small, MODEL, out)
    await write_file(out, target)
    assert compare_files(WALLS, target).identical
