# This file was generated with the assistance of an AI coding tool.
"""The table of inverse attributes the graph side keeps without IfcOpenShell is IfcOpenShell's."""

from __future__ import annotations

import pytest

from ifc_db.inverses import inverses


@pytest.mark.parametrize("schema_name", ["IFC2X3", "IFC4", "IFC4X3_ADD2"])
def test_the_table_of_inverses_is_the_schemas(schema_name: str) -> None:
    wrapper = pytest.importorskip("ifcopenshell.ifcopenshell_wrapper", reason="IfcOpenShell is not installed")
    expected = sorted(
        (entity.name(), inverse.name(), inverse.entity_reference().name(), inverse.attribute_reference().name())
        for entity in wrapper.schema_by_name(schema_name).entities()
        for inverse in entity.inverse_attributes()
    )
    assert sorted(inverses(schema_name)) == expected
