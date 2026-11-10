# This file was generated with the assistance of an AI coding tool.
"""Paths from a GlobalId: how they are written, and that every relationship type gives its attribute name back."""

from __future__ import annotations

import pytest

from ifc_lpg.paths import MAX_STEPS, PathError, Step, attribute_name, parse
from ifc_lpg.schema import relationship_type


def test_a_path_is_attribute_names_with_positions() -> None:
    steps = parse("Representation.Representations[0].Items[0].StyledByItem[0].Styles[0]")
    assert steps[0] == Step("Representation", ()) and steps[1] == Step("Representations", (0,))
    assert str(steps[3]) == "StyledByItem[0]" and len(steps) == 5
    assert parse("") == ()
    assert parse("CoordList[2][1]") == (Step("CoordList", (2, 1)),)


@pytest.mark.parametrize("path", ["Items[", "0Items", "Items[-1]", "Items[a]", "Items..Styles", ".Items", "Items[0] "])
def test_what_is_not_a_path_is_refused(path: str) -> None:
    with pytest.raises(PathError, match="not an attribute name"):
        parse(path)


def test_a_path_has_a_bound() -> None:
    with pytest.raises(PathError, match=f"more than {MAX_STEPS} steps"):
        parse(".".join(["Items"] * (MAX_STEPS + 1)))


def test_every_reference_attribute_name_comes_back_from_its_relationship_type() -> None:
    wrapper = pytest.importorskip("ifcopenshell.ifcopenshell_wrapper", reason="IfcOpenShell is not installed")
    checked = 0
    for schema_name in ("IFC2X3", "IFC4", "IFC4X3_ADD2"):
        schema = wrapper.schema_by_name(schema_name)
        for entity in schema.entities():
            names = [a.name() for a in entity.all_inverse_attributes()]
            for attribute in entity.all_attributes():
                declared = attribute.type_of_attribute()
                while declared.as_aggregation_type():
                    declared = declared.as_aggregation_type().type_of_element()
                named = declared.as_named_type()
                if named and (named.declared_type().as_entity() or named.declared_type().as_select_type()):
                    names.append(attribute.name())
            for name in names:
                assert attribute_name(relationship_type(name)) == name, (schema_name, entity.name(), name)
                checked += 1
    assert checked > 10_000
