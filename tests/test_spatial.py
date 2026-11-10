# This file was generated with the assistance of an AI coding tool.
"""The geometry records: one pass of IfcOpenShell, a box per product in project coordinates, and a file that is whole."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import pytest

from ifc_lpg.files import ConversionFailed
from ifc_lpg.graph import schema_statements
from ifc_lpg.spatial import FORMAT, SpatialError, box_of, read_records, spatial_file
from tests.conftest import FIXTURES_DIR

SPATIAL = FIXTURES_DIR / "spatial.ifc"
STOREY = "0Spatial00000000000004"


def _strict(name: str) -> Any:
    raise ValueError(name)


def geometry_of(tmp_path: Path) -> tuple[list[dict[str, Any]], Path]:
    pytest.importorskip("ifcopenshell", reason="IfcOpenShell is not installed")
    from ifc_lpg.spatial import write_geometry

    out = tmp_path / "geometry.jsonl"
    write_geometry(SPATIAL, out)
    lines = out.read_text(encoding="utf-8").splitlines()
    return [json.loads(line, parse_constant=_strict) for line in lines], out


def _first_mesh(lines: list[str]) -> str:
    return next(line for line in lines if line.startswith('{"t": "m"'))


def close(found: list[float], expected: list[float]) -> bool:
    return all(math.isclose(a, b, abs_tol=1e-9) for a, b in zip(found, expected, strict=True))


def test_one_pass_gives_each_product_its_storey_mesh_matrix_and_box(tmp_path: Path) -> None:
    records, _ = geometry_of(tmp_path)
    header, end = records[0], records[-1]
    assert header["t"] == "h" and header["format"] == FORMAT and header["version"] == 1
    assert header["schema"] == "IFC4" and header["units"] == "m" and header["coordinates"] == "project"
    assert header["source"] == {"sha256": hashlib.sha256(SPATIAL.read_bytes()).hexdigest()}
    assert end["t"] == "end" and (end["products"], end["meshes"], end["failed"]) == (7, 6, 1) and end["seconds"] >= 0
    meshes = {r["id"]: r for r in records if r["t"] == "m"}
    products = {r["global_id"][-2:]: r for r in records if r["t"] == "p"}
    seen: set[str] = set()
    for record in records:
        if record["t"] == "p":
            assert record["mesh"] in seen, "a mesh comes before the first product that uses it"
        elif record["t"] == "m":
            seen.add(record["id"])

    wall = products["10"]
    assert (wall["class"], wall["storey"], wall["matrix"][12:15]) == ("IfcWall", STOREY, [2.0, 0.0, 0.0])
    assert close(wall["bbox_min"], [0, -0.1, 0]) and close(wall["bbox_max"], [4, 0.1, 3])
    assert close(wall["bbox_centre"], [2, 0, 1.5])
    brick = meshes[wall["mesh"]]["materials"][0]
    assert brick["diffuse"] == [0.6, 0.2, 0.1] and brick["transparency"] == 0.0
    assert len(meshes[wall["mesh"]]["verts"]) % 3 == 0 and len(meshes[wall["mesh"]]["faces"]) % 3 == 0

    column_a, column_b = products["11"], products["12"]
    assert column_a["mesh"] == column_b["mesh"], "a shared representation is one mesh, placed twice"
    assert close(column_a["bbox_min"], [5.85, -0.15, 0]) and close(column_b["bbox_max"], [8.15, 0.15, 3])

    assert products["13"]["storey"] == STOREY, "an opening, through the wall it is in"
    assert products["15"]["storey"] == STOREY, "a door, through the opening it fills"
    assert close(products["15"]["bbox_min"], [2.55, -0.025, 0]) and close(
        products["15"]["bbox_max"], [3.45, 0.025, 2.1]
    )
    assert products["18"]["storey"] == STOREY, "a flight, through the stair it is part of"
    assert close(products["18"]["bbox_min"], [9.5, -1.5, 0])
    assert products["20"]["storey"] is None, "outside a storey"
    assert meshes[products["20"]["mesh"]]["materials"][0]["transparency"] is None, "none given, not NaN"

    [failed] = [r for r in records if r["t"] == "x"]
    assert failed["global_id"].endswith("22") and failed["error"] == "IfcOpenShell built no shape"


def test_a_box_goes_through_the_matrix_column_by_column() -> None:
    quarter_turn = (0.0, 1.0, 0.0, 0.0, -1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 10.0, 20.0, 30.0, 1.0)
    low, high = box_of((0.0, 0.0, 0.0, 2.0, 1.0, 3.0), quarter_turn)
    assert low == [9.0, 20.0, 30.0] and high == [10.0, 22.0, 33.0]


def test_the_records_are_read_back_whole(tmp_path: Path) -> None:
    _, out = geometry_of(tmp_path)
    header, products, end = read_records(out)
    assert header["format"] == FORMAT and len(products) == 7 and end["failed"] == 1
    assert set(products[0]) == {"step", "global_id", "bbox_min", "bbox_max", "bbox_centre"}


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda lines: lines[:-1], "cut short"),
        (lambda lines: [line for line in lines if line != _first_mesh(lines)], "does not count"),
        (lambda lines: [lines[0].replace('"version": 1', '"version": 2'), *lines[1:]], "version 1"),
        (lambda lines: [*lines, lines[-1]], "after the end"),
        (
            lambda lines: [lines[0], '{"t": "p", "step": 1, "global_id": "x", "bbox_min": [NaN, 0, 0]}', *lines[1:]],
            "no JSON",
        ),
        (
            lambda lines: [lines[0], '{"t": "p", "step": 1, "global_id": "x", "bbox_min": [0, 0]}', *lines[1:]],
            "three numbers",
        ),
        (lambda lines: [lines[0], '{"t": "q"}', *lines[1:]], "no known kind"),
    ],
)
def test_a_geometry_file_that_is_not_whole_is_refused(tmp_path: Path, change: Any, message: str) -> None:
    _, out = geometry_of(tmp_path)
    broken = tmp_path / "broken.jsonl"
    broken.write_text("\n".join(change(out.read_text(encoding="utf-8").splitlines())) + "\n", encoding="utf-8")
    with pytest.raises(SpatialError, match=message):
        read_records(broken)


async def test_the_worker_runs_in_a_process_of_its_own_and_refuses_a_file_cut_short(tmp_path: Path) -> None:
    pytest.importorskip("ifcopenshell", reason="IfcOpenShell is not installed")
    out = tmp_path / "geometry.jsonl"
    end = await spatial_file(SPATIAL, out, threads=2)
    assert (end["products"], end["meshes"], end["failed"]) == (7, 6, 1)
    assert read_records(out)[2] == end
    cut = tmp_path / "cut.ifc"
    data = SPATIAL.read_bytes()
    cut.write_bytes(data[: data.index(b"#70=")])
    with pytest.raises(ConversionFailed, match="the geometry failed"):
        await spatial_file(cut, tmp_path / "cut.jsonl")


def test_the_centres_have_a_point_index_on_both_databases() -> None:
    assert "CREATE POINT INDEX Entity_bbox_centre_point IF NOT EXISTS FOR (n:Entity) ON (n.bbox_centre)" in (
        schema_statements("neo4j")
    )
    assert "CREATE POINT INDEX ON :Entity(bbox_centre)" in schema_statements("memgraph")


class TransientError(Exception):
    pass


class _Busy:
    """A driver whose schema statements are refused while other queries run, as Memgraph refuses them."""

    def __init__(self, refusals: int) -> None:
        self.refusals, self.statements = refusals, []

    def session(self, database: str | None = None) -> Any:
        driver = self

        class Session:
            async def __aenter__(self) -> Any:
                return self

            async def __aexit__(self, *_: Any) -> None:
                return None

            async def run(self, statement: str) -> Any:
                if driver.refusals:
                    driver.refusals -= 1
                    raise TransientError("Cannot get unique access to the storage.")
                driver.statements.append(statement)

                class Result:
                    async def consume(self) -> None:
                        return None

                return Result()

        return Session()


async def test_making_the_point_index_anew_waits_for_sole_access(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from ifc_lpg.graph import DDL_ATTEMPTS, Neo4jGraph, rebuild_point_indexes

    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", sleep)
    driver = _Busy(refusals=5)
    await rebuild_point_indexes(Neo4jGraph(driver), "memgraph")
    assert driver.statements == [
        "DROP POINT INDEX ON :Entity(bbox_centre)",
        "CREATE POINT INDEX ON :Entity(bbox_centre)",
    ]
    assert waits == [0.25, 0.5, 1.0, 2.0, 4.0], "twice as long each time"
    with pytest.raises(TransientError):
        await rebuild_point_indexes(Neo4jGraph(_Busy(refusals=DDL_ATTEMPTS)), "memgraph")
    assert sum(waits[5:]) > 30, "about half a minute before it gives up"
