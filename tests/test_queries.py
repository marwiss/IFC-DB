# This file was generated with the assistance of an AI coding tool.
"""The named queries: what a statement may do, what its parameters must look like, and how an answer is put together."""

from __future__ import annotations

import asyncio
import re
from typing import Any

import pytest

from ifc_lpg.queries import (
    KINDS,
    MAX_GLOBAL_IDS,
    QUERIES,
    InvalidParameters,
    UnknownQuery,
    catalogue,
    json_value,
    property_values,
    run_query,
)

_WRITES = re.compile(r"\b(CREATE|MERGE|SET|DELETE|REMOVE|DROP|CALL|LOAD|FOREACH)\b", re.IGNORECASE)
WALL = "1Xezbq735AdBO_4DerG66G"


class Asked:
    """A graph that answers with the rows it was given, and keeps what it was asked."""

    def __init__(self, rows: list[dict[str, Any]], seconds: float = 0.0) -> None:
        self.rows, self.seconds = rows, seconds
        self.asked: list[tuple[str, dict[str, Any]]] = []

    async def run(self, query: str, /, **params: Any) -> list[dict[str, Any]]:
        self.asked.append((query, params))
        await asyncio.sleep(self.seconds)
        return self.rows


class Point(tuple):
    srid = 9157


@pytest.mark.parametrize("name", list(QUERIES))
def test_a_query_only_reads_names_its_model_by_id_and_limits_its_rows(name: str) -> None:
    cypher = QUERIES[name].cypher
    assert not _WRITES.search(cypher)
    assert cypher.lstrip().startswith("MATCH (m:Model {id: $model_id})")
    assert cypher.rstrip().endswith("LIMIT $row_limit")


@pytest.mark.parametrize("name", list(QUERIES))
def test_a_query_uses_the_parameters_it_declares_and_no_others(name: str) -> None:
    query = QUERIES[name]
    used = set(re.findall(r"\$([A-Za-z_]+)", query.cypher)) - {"model_id", "row_limit"}
    assert used == {parameter.name for parameter in query.parameters}
    assert all(parameter.kind in KINDS for parameter in query.parameters)


@pytest.mark.parametrize("name", list(QUERIES))
def test_a_query_returns_the_columns_it_declares(name: str) -> None:
    query = QUERIES[name]
    returned = query.body[query.body.rindex("RETURN") :].split("ORDER BY")[0]
    for column in query.columns:
        assert re.search(rf"\b{column}\b", returned), column


def test_the_catalogue_is_plain_data() -> None:
    listed = {query["name"]: query for query in catalogue()}
    assert list(listed) == list(QUERIES)
    assert listed["property"]["columns"] == ["global_id", "class", "name", "value", "type"]
    assert listed["property"]["parameters"][0] == {
        "name": "pset",
        "description": "The property set, such as Pset_WallCommon",
        "required": True,
        "kind": "text",
    }
    assert [parameter["kind"] for parameter in listed["entity"]["parameters"]] == ["global_id"]


def test_a_class_must_be_the_name_of_one() -> None:
    assert QUERIES["elements"].bind({"class": "IfcWall"}) == {"class": "IfcWall"}
    for raw in ("Wall", "IfcWall) DETACH DELETE (n", "Ifc", "ifcwall"):
        with pytest.raises(InvalidParameters, match="name of an IFC class"):
            QUERIES["elements"].bind({"class": raw})


def test_a_global_id_must_look_like_one() -> None:
    assert QUERIES["entity"].bind({"global_id": WALL}) == {"global_id": WALL}
    for raw in ("short", WALL + "0", "1Xezbq735AdBO_4DerG66 "):
        with pytest.raises(InvalidParameters, match="22 characters"):
            QUERIES["entity"].bind({"global_id": raw})


def test_a_required_parameter_must_be_given_and_an_unknown_one_is_refused() -> None:
    with pytest.raises(InvalidParameters, match="needs the parameter pset"):
        QUERIES["property"].bind({"property": "GTIN"})
    with pytest.raises(InvalidParameters, match="takes no parameter depth"):
        QUERIES["storeys"].bind({"depth": "2"})
    assert QUERIES["materials"].bind({}) == {"class": None}
    assert QUERIES["materials"].bind({"class": ""}) == {"class": None}


def test_a_text_parameter_is_bounded_and_has_no_control_characters() -> None:
    for raw in ("x" * 256, "Pset\nWall", "Pset\x00"):
        with pytest.raises(InvalidParameters, match="1 to 255 characters"):
            QUERIES["property"].bind({"pset": raw, "property": "GTIN"})


async def test_an_answer_has_its_rows_in_the_order_of_the_columns() -> None:
    graph = Asked(
        [{"count": 3, "class": "IfcWall"}, {"count": 1, "class": "IfcDoor"}, {"count": 1, "class": "IfcSlab"}]
    )
    answer = await run_query(graph, "m", "element-classes", max_rows=2)
    assert answer == {
        "name": "element-classes",
        "columns": ["class", "count"],
        "rows": [["IfcWall", 3], ["IfcDoor", 1]],
        "truncated": True,
    }
    (query, params) = graph.asked[0]
    assert query == QUERIES["element-classes"].cypher and params == {"model_id": "m", "row_limit": 3}
    assert not (await run_query(graph, "m", "element-classes"))["truncated"]


async def test_nothing_is_asked_when_the_name_or_the_parameters_are_wrong() -> None:
    graph = Asked([])
    with pytest.raises(UnknownQuery):
        await run_query(graph, "m", "everything")
    with pytest.raises(InvalidParameters):
        await run_query(graph, "m", "elements", {"class": "Wall"})
    with pytest.raises(InvalidParameters, match="max_rows"):
        await run_query(graph, "m", "storeys", max_rows=0)
    assert graph.asked == []


async def test_a_query_that_takes_too_long_is_given_up() -> None:
    with pytest.raises(TimeoutError):
        await run_query(Asked([], seconds=5), "m", "storeys", timeout_seconds=0.01)


async def test_a_graph_that_can_stop_a_statement_itself_is_asked_to() -> None:
    class Stopping(Asked):
        async def read(self, query: str, params: dict[str, Any], *, timeout_seconds: float) -> list[dict[str, Any]]:
            self.asked.append((query, {**params, "timeout_seconds": timeout_seconds}))
            return self.rows

    graph = Stopping([{"storey": "Plan 1", "elevation": 0.0, "spaces": 2, "net_floor_area": 32.5}])
    answer = await run_query(graph, "m", "areas", timeout_seconds=3.0)
    assert answer["rows"] == [["Plan 1", 0.0, 2, 32.5]]
    assert graph.asked[0][1] == {"model_id": "m", "row_limit": 10_001, "timeout_seconds": 3.0}


def test_values_are_what_json_has() -> None:
    assert json_value(Point((1.0, 2.0, 3.0))) == {"srid": 9157, "x": 1.0, "y": 2.0, "z": 3.0}
    assert json_value({"a": [float("nan"), 1.5, None], "b": (True, "x")}) == {"a": [None, 1.5, None], "b": [True, "x"]}
    assert json_value(b"\x00\x01") == "AAE="


async def test_a_property_for_a_list_of_global_ids_is_checked_first() -> None:
    graph = Asked([{"global_id": WALL, "value": "3", "type": "IFCLABEL"}])
    other = "3eT96F_cnDsOufCEsotwtp"
    found = await property_values(graph, "m", pset="Reuse", property="GTIN", global_ids=[other, WALL, WALL])
    assert found == [{"global_id": WALL, "value": "3", "type": "IFCLABEL"}]
    assert graph.asked[0][1] == {"model_id": "m", "pset": "Reuse", "property": "GTIN", "global_ids": [WALL, other]}
    assert await property_values(graph, "m", pset="Reuse", property="GTIN", global_ids=[]) == []
    with pytest.raises(InvalidParameters, match="22 characters"):
        await property_values(graph, "m", pset="Reuse", property="GTIN", global_ids=["nope"])
    with pytest.raises(InvalidParameters, match="1 to 255"):
        await property_values(graph, "m", pset="", property="GTIN", global_ids=[WALL])
    many = [f"{number:022d}" for number in range(MAX_GLOBAL_IDS + 1)]
    with pytest.raises(InvalidParameters, match="at most"):
        await property_values(graph, "m", pset="Reuse", property="GTIN", global_ids=many)
    assert len(graph.asked) == 1
