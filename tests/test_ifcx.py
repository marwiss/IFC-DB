# This file was generated with the assistance of an AI coding tool.
"""IFC5 files composed, into the records of a graph and back, without a database. The files are written here."""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from typing import Any

import pytest

from ifc_db.ifcx import (
    MAX_DEPTH,
    IfcxError,
    compose,
    decode_attributes,
    document_of,
    encode_attributes,
    main,
    overlay_of,
    read_layer,
    records,
    same,
)

WALL, STOREY, PROJECT, TYPE, BODY, SPACE = "w-1", "s-1", "p-1", "t-1", "b-1", "r-1"
BASE: dict[str, Any] = {
    "header": {"id": "example/base.ifcx", "ifcxVersion": "ifcx_alpha", "author": "tests"},
    "imports": [{"uri": "https://example.org/schemas/ifc@v5a.ifcx"}],
    "schemas": {"example::note": {"value": {"dataType": "String"}}},
    "data": [
        {"path": PROJECT, "children": {"Storey": STOREY}, "attributes": {"bsi::ifc::class": {"code": "IfcProject"}}},
        {"path": STOREY, "children": {"Wall": WALL, "Missing": "nowhere"}},
        {
            "path": WALL,
            "inherits": {"type": TYPE},
            "attributes": {
                "bsi::ifc::class": {"code": "IfcWall", "uri": "https://example.org/class/IfcWall"},
                "bsi::ifc::prop::IsExternal": True,
                "bsi::ifc::prop::Height": 3,
                "bsi::ifc::prop::Volume": 1.5,
                "usd::xformop": {"transform": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [2.5, 0, -0.0, 1]]},
                "example::gwp": {"A1-A3": -629.4, "C": 12, "D": None, "stages": ["A", "C"]},
                "example::boundary": {"relating": {"ref": SPACE}},
            },
        },
        {"path": TYPE, "children": {"Body": BODY}, "attributes": {"example::note": "shared"}},
        {
            "path": BODY,
            "attributes": {
                "usd::usdgeom::mesh": {
                    "points": [[0, -0.6, 0], [5.002221e-12, 0.59999996, 3], [1.7976931348623157e308, 5e-324, -0.0]],
                    "faceVertexIndices": [0, 1, 2],
                }
            },
        },
        {"path": SPACE, "attributes": {"bsi::ifc::class": {"code": "IfcSpace"}, "example::note": "first"}},
    ],
}
OVERLAY: dict[str, Any] = {
    "header": {"id": "example/overlay.ifcx", "ifcxVersion": "ifcx_alpha", "author": "tests"},
    "imports": [{"uri": "example/base.ifcx"}, {"uri": "https://example.org/schemas/ifc@v5a.ifcx"}],
    "schemas": {},
    "data": [
        {"path": WALL, "attributes": {"bsi::ifc::prop::Height": 3.25, "bsi::ifc::prop::Volume": None}},
        {"path": SPACE, "attributes": {"example::note": "second"}},
        {"path": STOREY, "children": {"Missing": None}},
    ],
}


def write(tmp_path: Path, name: str, document: dict[str, Any]) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def as_the_graph_holds_them(found: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The records as the export reads them: the points as tuples, and the relationships per node."""
    nodes = {
        record["path"]: {
            "path": record["path"],
            "props": {
                **record["props"],
                **{key: [tuple(row) for row in rows] for key, rows in record["points"].items()},
            },
            "links": [],
        }
        for record in found
        if record["t"] == "n"
    }
    for record in found:
        if record["t"] == "e" and record["type"] != "REF":
            nodes[record["a"]]["links"].append([record["type"], record["name"], record["b"]])
    return list(nodes.values())


def test_the_files_are_composed_after_what_they_import() -> None:
    composed = compose([("overlay.ifcx", OVERLAY), ("base.ifcx", BASE)])
    assert composed.layers == ["example/base.ifcx", "example/overlay.ifcx"]
    wall = composed.objects[WALL]["attributes"]
    assert same(wall["bsi::ifc::prop::Height"], 3.25), "the later value wins"
    assert "bsi::ifc::prop::Volume" not in wall, "null takes one away"
    assert composed.objects[STOREY]["children"] == {"Wall": WALL}
    assert composed.objects[SPACE]["attributes"]["example::note"] == "second"
    assert {(c["path"], c["name"]) for c in composed.conflicts} == {
        (WALL, "bsi::ifc::prop::Height"),
        (WALL, "bsi::ifc::prop::Volume"),
        (SPACE, "example::note"),
        (STOREY, "Missing"),
    }
    assert composed.imports == [{"uri": "https://example.org/schemas/ifc@v5a.ifcx"}], "what the files are is no import"
    assert [w["kind"] for w in composed.warnings] == ["import_unavailable"]
    assert composed.header == OVERLAY["header"]


def test_files_that_import_each_other_are_refused() -> None:
    first = {**BASE, "imports": [{"uri": "example/overlay.ifcx"}]}
    with pytest.raises(IfcxError, match="circle"):
        compose([("base.ifcx", first), ("overlay.ifcx", OVERLAY)])


VALUES = [
    pytest.param("text", "x", id="a string"),
    pytest.param("flag", False, id="a boolean"),
    pytest.param("count", 3, id="an integer"),
    pytest.param("count", 3.0, id="a float that looks like one"),
    pytest.param("zero", -0.0, id="a negative zero"),
    pytest.param("big", 2**63, id="an integer no database holds"),
    pytest.param("object", {"code": "IfcWall", "n": {"m": 1, "f": 1.0, "none": None}}, id="nested objects"),
    pytest.param("empty", {}, id="an empty object"),
    pytest.param("list", [], id="an empty list"),
    pytest.param("names", ["a", "b"], id="strings"),
    pytest.param("ints", [1, 2, 3], id="integers"),
    pytest.param("mixed", [0, 0.5, 1], id="integers among floats"),
    pytest.param("anything", [1, "a", None, {"b": [2]}], id="a list of anything"),
    pytest.param("points", [[0, 1.5, 2], [3, 4, 5.25]], id="rows of three"),
    pytest.param("plane", [[0.5, 1.5], [2.5, 3.5]], id="rows of two"),
    pytest.param("matrix", [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], id="rows of four integers"),
    pytest.param("ragged", [[1, 2], [3]], id="rows of different length"),
    pytest.param("huge", [[2**60, 0, 0]], id="an integer a double cannot hold, in a row"),
    pytest.param("a.b", 1, id="a key with a dot"),
    pytest.param("path", "p", id="a key the node uses itself"),
    pytest.param("member", {"a.b": 1}, id="a member with a dot"),
    pytest.param("with`tick", [[1.0, 2.0, 3.0]], id="points under a key no query may name"),
]


@pytest.mark.parametrize(("key", "value"), VALUES)
def test_every_value_comes_back_with_its_type(key: str, value: Any) -> None:
    props, points = encode_attributes({key: value})
    stored = {**props, **{name: [tuple(row) for row in rows] for name, rows in points.items()}}
    assert all(not isinstance(item, dict) for item in props.values()), "a database holds no maps here"
    assert same(json.loads(json.dumps(decode_attributes(stored))), {key: value})


def test_a_value_goes_where_a_query_looks_for_it() -> None:
    attributes = compose([("base.ifcx", BASE)]).objects[WALL]["attributes"]
    props, points = encode_attributes(attributes)
    assert props["bsi::ifc::class.code"] == "IfcWall"
    assert props["bsi::ifc::prop::Height"] == 3 and props["bsi::ifc::prop::IsExternal"] is True
    assert props["usd::xformop.transform"] == [1.0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 2.5, 0, -0.0, 1]
    assert props["usd::xformop.transform#shape"] == [4, 4]
    assert props["example::gwp.A1-A3"] == -629.4 and props["example::gwp.D#null"] is True
    assert props["example::boundary.relating.ref"] == SPACE
    assert not points
    mesh = encode_attributes(compose([("base.ifcx", BASE)]).objects[BODY]["attributes"])
    assert mesh[1]["usd::usdgeom::mesh.points"][0] == [0.0, -0.6, 0.0]
    assert mesh[0]["usd::usdgeom::mesh.points#ints"] == [0, 2, 5]
    assert mesh[0]["usd::usdgeom::mesh.faceVertexIndices"] == [0, 1, 2]


def test_a_path_becomes_a_node_and_its_names_relationships() -> None:
    found = list(records(compose([("base.ifcx", BASE), ("overlay.ifcx", OVERLAY)])))
    nodes = {record["path"]: record for record in found if record["t"] == "n"}
    assert nodes[WALL]["labels"] == ["IfcWall"] and nodes[TYPE]["labels"] == []
    edges = {(record["type"], record["a"], record["name"], record["b"]) for record in found if record["t"] == "e"}
    assert edges == {
        ("CHILD", PROJECT, "Storey", STOREY),
        ("CHILD", STOREY, "Wall", WALL),
        ("CHILD", TYPE, "Body", BODY),
        ("INHERITS", WALL, "type", TYPE),
        ("REF", WALL, "example::boundary.relating", SPACE),
    }


def test_the_graph_gives_back_the_same_composition(tmp_path: Path) -> None:
    composed = compose([("base.ifcx", BASE), ("overlay.ifcx", OVERLAY)])
    found = list(records(composed))
    header = found[0]
    model = {key: header[key] for key in ("header", "imports", "schemas")}
    written = document_of(json.loads(json.dumps(model)), iter(json.loads(json.dumps(as_the_graph_holds_them(found)))))
    assert [entry["path"] for entry in written["data"]] == sorted(composed.objects), "one entry per path"
    again = compose([("written.ifcx", written)])
    assert same(again.objects, composed.objects)
    assert again.header == composed.header and again.imports == composed.imports and again.schemas == composed.schemas


def test_a_child_with_no_entry_of_its_own_is_kept() -> None:
    document = {"header": {}, "imports": [], "schemas": {}, "data": [{"path": "a", "children": {"x": "b"}}]}
    found = list(records(compose([("one.ifcx", document)])))
    assert {"t": "n", "path": "b", "labels": [], "props": {"#stub": True}, "points": {}} in found
    written = document_of({"header": {}, "imports": [], "schemas": {}}, iter(as_the_graph_holds_them(found)))
    assert written["data"] == [{"path": "a", "children": {"x": "b"}}]


def test_a_date_on_a_node_is_no_attribute() -> None:
    node = {"path": "a", "props": {"checked": {"$native": "DateTime"}}, "links": []}
    with pytest.raises(ValueError, match=r"^a: checked holds a DateTime"):
        document_of({"header": {}, "imports": [], "schemas": {}}, iter([node]))


@pytest.mark.parametrize(
    ("data", "says"),
    [
        pytest.param(b"{", "no JSON", id="not JSON"),
        pytest.param(b'{"data": [{"path": "a", "attributes": {"x": NaN}}]}', "no JSON number", id="NaN"),
        pytest.param(b'{"data": {}}', "no list of data", id="no list of data"),
        pytest.param(b'{"data": [{"attributes": {}}]}', "with a path", id="an entry without a path"),
        pytest.param(b'{"data": [{"path": "a", "children": {"x": 1}}]}', "are paths", id="a child that is no path"),
        pytest.param(b'{"data": [], "imports": [{}]}', "with a uri", id="an import without a uri"),
        pytest.param(b'{"data": [' + b"[" * MAX_DEPTH + b"]" * MAX_DEPTH + b"]}", "nests deeper", id="too deep"),
        # Python's parser gives up before 3.14, and from 3.14 it reads it, which the depth check then refuses.
        pytest.param(
            b'{"data": [' + b"[" * 100_000 + b"]" * 100_000 + b"]}", "no JSON|nests deeper", id="deeper than Python"
        ),
        pytest.param(b"\xff\xfe", "no JSON", id="not UTF-8"),
    ],
)
def test_what_is_no_ifcx_is_refused(data: bytes, says: str) -> None:
    with pytest.raises(IfcxError, match=says):
        read_layer(data)


def test_reading_and_writing_run_as_processes_would(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out, err = StringIO(), StringIO()
    monkeypatch.setattr("sys.stdout", out)
    monkeypatch.setattr("sys.stderr", err)
    base, overlay, found = write(tmp_path, "base.ifcx", BASE), write(tmp_path, "overlay.ifcx", OVERLAY), tmp_path / "r"
    assert main(["read", str(overlay), str(base), str(found)]) == 0
    header = json.loads(out.getvalue())
    assert header["layers"] == ["example/base.ifcx", "example/overlay.ifcx"] and header["objects"] == 6
    lines = [json.loads(line) for line in found.read_text(encoding="utf-8").splitlines()]
    exported = tmp_path / "exported"
    model = {key: lines[0][key] for key in ("header", "imports", "schemas")}
    exported.write_text(
        "\n".join(json.dumps(item) for item in [model, *as_the_graph_holds_them(lines[1:])]), encoding="utf-8"
    )
    target = tmp_path / "written.ifcx"
    assert main(["write", str(exported), str(target)]) == 0
    again = compose([("written.ifcx", read_layer(target.read_bytes()))])
    assert same(again.objects, compose([("base.ifcx", BASE), ("overlay.ifcx", OVERLAY)]).objects)

    (tmp_path / "bad.ifcx").write_bytes(b"[]")
    assert main(["read", str(tmp_path / "bad.ifcx"), str(found)]) == 3
    assert "no list of data" in json.loads(err.getvalue().splitlines()[-1])["error"]
    assert main(["read", str(tmp_path / "missing.ifcx"), str(found)]) == 3
    assert main(["read", str(base), str(found), "--max-entries", "2"]) == 3


def _document(composed: Any) -> dict[str, Any]:
    data = [{"path": path, **parts} for path, parts in sorted(composed.objects.items())]
    return {"header": composed.header, "imports": composed.imports, "schemas": composed.schemas, "data": data}


def test_a_layer_holds_only_what_changed_and_composes_to_the_change() -> None:
    composed = compose([("base.ifcx", BASE), ("overlay.ifcx", OVERLAY)])
    base = _document(composed)
    current = json.loads(json.dumps(base))
    wall = next(entry for entry in current["data"] if entry["path"] == WALL)
    wall["attributes"]["bsi::ifc::prop::IsExternal"] = False
    del wall["attributes"]["example::gwp"]
    next(entry for entry in current["data"] if entry["path"] == STOREY)["children"]["Door"] = "d-1"
    current["data"].append(
        {"path": "d-1", "children": {}, "inherits": {}, "attributes": {"bsi::ifc::class": {"code": "IfcDoor"}}}
    )
    layer = overlay_of(base, current, "example/change.ifcx", composed.layers)
    assert layer["header"]["id"] == "example/change.ifcx"
    assert [item["uri"] for item in layer["imports"]][:2] == ["example/base.ifcx", "example/overlay.ifcx"]
    assert {entry["path"] for entry in layer["data"]} == {WALL, STOREY, "d-1"}, "only what changed"
    assert layer["data"][-1]["attributes"] == {"bsi::ifc::prop::IsExternal": False, "example::gwp": None}
    again = compose([("base.ifcx", BASE), ("overlay.ifcx", OVERLAY), ("change.ifcx", layer)])
    assert same(again.objects, compose([("current.ifcx", current)]).objects)
    gone = json.loads(json.dumps(base))
    gone["data"] = [entry for entry in gone["data"] if entry["path"] != SPACE]
    removed = overlay_of(base, gone, "example/gone.ifcx", composed.layers)
    assert removed["data"] == [{"path": SPACE, "attributes": {"bsi::ifc::class": None, "example::note": None}}], (
        "a path cannot be taken away in IFCX, so all of it is set to null"
    )
