# This file was generated with the assistance of an AI coding tool.
"""IFC5 attributes against their schemas: the data types, the pinned imports and the worker. The schemas are our own."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ifc_db.ifcx import compose
from ifc_db.ifcx_check import MANIFEST, PINNED, check, check_ifcx, problems, read_schemas

PINNED_URI = "https://ifcx.dev/@standards.buildingsmart.org/ifc/core/prop@v5a.ifcx"
SCHEMAS = {
    "example::height": {"value": {"dataType": "Real", "quantityKind": "Length"}},
    "example::kind": {"value": {"dataType": "Enum", "enumRestrictions": {"options": ["WALL", "SLAB"]}}},
    "example::mesh": {
        "value": {
            "dataType": "Object",
            "objectRestrictions": {
                "values": {
                    "points": {
                        "dataType": "Array",
                        "arrayRestrictions": {
                            "value": {"dataType": "Array", "arrayRestrictions": {"value": {"dataType": "Real"}}}
                        },
                    },
                    "name": {"dataType": "String", "optional": True},
                }
            },
        }
    },
}


def layer(attributes: dict[str, object], imports: tuple[str, ...] = ()) -> dict[str, object]:
    return {
        "header": {"id": "example/check.ifcx"},
        "imports": [{"uri": uri} for uri in imports],
        "schemas": SCHEMAS,
        "data": [{"path": "wall", "attributes": attributes}],
    }


def test_each_data_type_is_checked() -> None:
    assert problems(2.5, {"dataType": "Real"}, "h") == []
    assert problems(True, {"dataType": "Real"}, "h") == ["h is true, not a Real"], "a boolean is no number"
    assert problems(3, {"dataType": "Integer"}, "n") == [] and problems(3.0, {"dataType": "Integer"}, "n")
    assert problems("x", {"dataType": "Boolean"}, "b") == ['b is "x", not a Boolean']
    assert problems({"ref": "a"}, {"dataType": "Reference"}, "r") == [] and problems(
        "a", {"dataType": "Reference"}, "r"
    )
    assert problems("x", {"dataType": "Blob"}, "z") == [
        "z has a schema of the data type 'Blob', which this check does not know"
    ]


def test_a_composition_is_checked_against_its_own_schemas_and_the_imports() -> None:
    good = layer({"example::height": 3.0, "example::kind": "WALL", "example::mesh": {"points": [[0.0, 1.0, 2.0]]}})
    assert check(compose([("a.ifcx", good)]), {}) == []
    bad = layer(
        {
            "example::height": "tall",
            "example::kind": "ROOF",
            "example::mesh": {"points": [[0.0, "y", 2.0]], "colour": "red"},
            "example::unknown": 1,
            "bsi::ifc::prop::IsExternal": "yes",
        },
        imports=(PINNED_URI,),
    )
    imported = {PINNED_URI: {"bsi::ifc::prop::IsExternal": {"value": {"dataType": "Boolean"}}}}
    issues = check(compose([("a.ifcx", bad)]), imported)
    assert [(i["severity"], i["attribute"], i["message"]) for i in issues] == [
        ("error", "bsi::ifc::prop::IsExternal", 'bsi::ifc::prop::IsExternal is "yes", not a Boolean'),
        ("error", "example::height", 'example::height is "tall", not a Real'),
        ("error", "example::kind", 'example::kind is "ROOF", not one of WALL, SLAB'),
        ("error", "example::mesh", "example::mesh.colour is no member the schema has"),
        ("error", "example::mesh", 'example::mesh.points[0][1] is "y", not a Real'),
        ("error", "example::unknown", "no schema says what example::unknown is"),
    ]
    missing = check(
        compose([("a.ifcx", layer({"example::mesh": {}}, imports=("https://example.org/other.ifcx",)))]), {}
    )
    assert [(i["severity"], i["message"]) for i in missing] == [
        ("warning", "the import https://example.org/other.ifcx is not pinned, so its schemas are unknown"),
        ("error", "example::mesh.points is not there"),
    ], "an optional member may be missing, and one that is not may not"


def test_the_pins_are_https_and_sha256() -> None:
    assert PINNED and all(uri.startswith("https://") and len(sha) == 64 for uri, sha in PINNED.items())


def test_a_schema_that_is_not_the_pinned_one_is_refused(tmp_path: Path) -> None:
    good = json.dumps({"header": {}, "imports": [], "schemas": {}, "data": []}).encode()
    (tmp_path / "x.ifcx").write_bytes(good)
    (tmp_path / MANIFEST).write_text(json.dumps({PINNED_URI: "x.ifcx"}), encoding="utf-8")
    assert hashlib.sha256(good).hexdigest() != PINNED[PINNED_URI]
    with pytest.raises(ValueError, match="not the pinned schema"):
        read_schemas(tmp_path)


async def test_the_check_runs_in_a_worker(tmp_path: Path) -> None:
    source = tmp_path / "check.ifcx"
    source.write_text(json.dumps(layer({"example::height": "tall"})), encoding="utf-8")
    answer = await check_ifcx([source])
    assert (answer["issue_count"], answer["error_count"]) == (1, 1)
    assert answer["issues"][0] == {
        "kind": "ifcx-schema",
        "severity": "error",
        "message": 'example::height is "tall", not a Real',
        "path": "wall",
        "attribute": "example::height",
    }
