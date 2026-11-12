# This file was generated with the assistance of an AI coding tool.
"""Relationship types of the IFC graph follow the graph's convention: upper case with underscores."""

import json
from pathlib import Path
from typing import Any

import pytest

from ifc_db.graph import FORM_VERSION, is_whole
from ifc_db.importer import MODEL_FACTS, import_records, model_facts, models, relationship_type


class Asked:
    """A graph that answers with the rows it was given, and keeps what it was asked."""

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []
        self.asked: list[tuple[str, dict[str, Any]]] = []

    async def run(self, query: str, /, **params: Any) -> list[dict[str, Any]]:
        self.asked.append((query, params))
        return self.rows


@pytest.mark.parametrize(
    ("attribute", "expected"),
    [
        ("IsDefinedBy", "IS_DEFINED_BY"),
        ("HasProperties", "HAS_PROPERTIES"),
        ("RelatingPropertyDefinition", "RELATING_PROPERTY_DEFINITION"),
        ("ContainedInStructure", "CONTAINED_IN_STRUCTURE"),
        ("ObjectPlacement", "OBJECT_PLACEMENT"),
        ("Items", "ITEMS"),
        ("HasAssignments", "HAS_ASSIGNMENTS"),
        ("RelatedObjects", "RELATED_OBJECTS"),
        ("IFCData", "IFC_DATA"),  # an acronym stays one word
        ("Axis2Placement", "AXIS2_PLACEMENT"),
    ],
)
def test_attribute_names_become_upper_snake_case(attribute: str, expected: str) -> None:
    assert relationship_type(attribute) == expected


def test_unsafe_names_are_refused() -> None:
    with pytest.raises(ValueError):
        relationship_type("Is-Defined By")


async def test_the_import_makes_the_model_and_says_when_it_is_whole(tmp_path: Path) -> None:
    guid = "0" * 22
    records = tmp_path / "records.jsonl"
    lines = [
        {
            "t": "n",
            "step": 1,
            "labels": ["IfcProject", "IfcRoot"],
            "props": {"ifc_class": "IfcProject", "GlobalId": guid},
        },
        {
            "t": "h",
            "schema": "IFC4",
            "entities": 1,
            "relationships": 0,
            "skipped": 0,
            "ifc_project_guid": guid,
            "classes": {"IfcProject": 1},
            "lossless": True,
            "geometry": "skeleton",
            "ifc_header": "[]",
        },
    ]
    records.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    graph = Asked()
    facts = await import_records(graph, "m", records)
    first, last = graph.asked[0], graph.asked[-1]
    assert first[0].startswith("MERGE (m:Model {id: $model_id})") and first[0].endswith("REMOVE m.imported_at")
    assert first[1] == {"model_id": "m", "form": FORM_VERSION}
    assert "SET m += $facts" in last[0] and last[1]["facts"] == {key: facts[key] for key in MODEL_FACTS}
    assert facts["nodes"] == facts["entities"] == 1 and facts["relationships"] == 0
    assert (facts["format"], facts["schema"], facts["geometry"], facts["lossless"]) == (
        "step",
        "IFC4",
        "skeleton",
        True,
    )
    assert facts["ifc_project_guid"] == guid and facts["imported_at"].endswith("Z")


async def test_the_facts_of_a_model_are_the_packages_own() -> None:
    properties = {
        "id": "m",
        "schema": "IFC4",
        "imported_at": "2026-11-11T12:00:00.000Z",
        "project_id": "p",
        "state": "queued",
        "spatial_boxes": 3,
        "prepared_from": "b",
        "merge_of": ["a", "b"],
    }
    assert await model_facts(Asked([{"properties": properties}]), "m") == {
        "id": "m",
        "whole": True,
        "imported_at": "2026-11-11T12:00:00.000Z",
        "merge_of": ["a", "b"],
        "prepared_from": "b",
        "schema": "IFC4",
        "spatial_boxes": 3,
    }
    assert await model_facts(Asked(), "m") is None


async def test_the_models_of_a_graph_are_listed_with_whether_each_is_whole() -> None:
    graph = Asked(
        [
            {"id": "a", "imported_at": "2026-11-11T12:00:00.000Z", "state": None},
            {"id": "b", "imported_at": None, "state": "done"},
            {"id": "c", "imported_at": None, "state": "running"},
        ]
    )
    assert await models(graph) == [{"id": "a", "whole": True}, {"id": "b", "whole": True}, {"id": "c", "whole": False}]
    ((statement, params),) = graph.asked
    assert "ORDER BY id" in statement and params == {}, "every model, in the order of the ids, and nothing else read"
    assert await models(Asked()) == []


def test_a_model_is_whole_by_its_mark_or_by_the_state_from_before() -> None:
    assert is_whole({"imported_at": "2026-11-11T12:00:00.000Z", "state": "queued"})
    assert is_whole({"state": "done"})
    assert not is_whole({"state": "running"}) and not is_whole({})
