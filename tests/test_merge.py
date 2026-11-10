# This file was generated with the assistance of an AI coding tool.
"""The parts of a merge that need no database: new GlobalIds, renumbering, and lists that lose a member."""

from __future__ import annotations

import json

import pytest

from ifc_lpg.merge import _edges_after, _Instance, _map_parameters, _map_tagged, new_global_id


def test_a_new_global_id_is_ifcs_form_of_128_bits() -> None:
    guid = pytest.importorskip("ifcopenshell.guid", reason="IfcOpenShell is not installed")
    found = {new_global_id() for _ in range(200)}
    assert len(found) == 200
    for global_id in found:
        assert len(global_id) == 22 and global_id[0] in "0123"
        assert guid.compress(guid.expand(global_id)) == global_id


def test_parameters_are_renumbered_and_their_lists_lose_what_is_dropped() -> None:
    parameters = [b"sName", [3, 4, 5], (b"IFCLABEL", [b"sx"]), 4]
    mapped = _map_parameters(parameters, lambda step: step + 100, {4})
    assert mapped == [b"sName", [103, 105], (b"IFCLABEL", [b"sx"]), 104], "an attribute of its own stays"


def test_tagged_json_is_renumbered_with_its_relationships() -> None:
    value = [{"r": 0}, {"d": 7}, {"r": 1}, {"d": 8}]
    positions = {0: None, 1: 0}  # the relationship at 0 is dropped, and the one at 1 moves to 0
    mapped = _map_tagged(value, lambda step: step * 10, {8}, positions)
    assert mapped == [{"d": 70}, {"r": 0}]
    nested = {"t": "IFCX", "v": [{"d": 7}]}
    assert _map_tagged(nested, lambda step: step + 1, set(), {}) == {"t": "IFCX", "v": [{"d": 8}]}
    assert json.loads(json.dumps(mapped)) == mapped


def test_a_list_of_relationships_closes_up_where_a_member_is_dropped() -> None:
    instance = _Instance(
        1,
        edges=[("RELATED_OBJECTS", 0, 10), ("RELATED_OBJECTS", 1, 11), ("RELATED_OBJECTS", 2, 12), ("NAME", 0, 9)],
    )
    kept, moved = _edges_after(instance, {11})
    assert kept == [("RELATED_OBJECTS", 0, 10), ("RELATED_OBJECTS", 1, 12), ("NAME", 0, 9)]
    assert moved == {"RELATED_OBJECTS": {0: 0, 1: None, 2: 1}}
    instance.edges = [("ROWS", [0, 0], 10), ("ROWS", [0, 1], 11)]
    with pytest.raises(ValueError, match="list of lists"):
        _edges_after(instance, {11})
