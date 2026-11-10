# This file was generated with the assistance of an AI coding tool.
"""Making a model ready to be merged: its lengths turned, and a value with a unit of its own left as it was."""

from __future__ import annotations

from pathlib import Path

import pytest

from ifc_lpg.prepare import prepare_file
from ifc_lpg.step import StepFile
from tests.conftest import FIXTURES_DIR


def test_lengths_turn_and_a_factor_with_a_unit_of_its_own_stays(tmp_path: Path) -> None:
    pytest.importorskip("ifcopenshell", reason="IfcOpenShell is not installed")
    target = tmp_path / "metres.ifc"
    answer = prepare_file(FIXTURES_DIR / "three_walls_gtin.ifc", target, schema=None, length_unit="METER")
    assert answer["length_unit"][1] == "METER" and answer["changed_count"] == 0
    written = StepFile(target.read_bytes())
    factors = {step: parameters for step, keyword, parameters in written if keyword == b"IFCMEASUREWITHUNIT"}
    original = StepFile((FIXTURES_DIR / "three_walls_gtin.ifc").read_bytes())
    assert factors == {
        step: parameters for step, keyword, parameters in original if keyword == b"IFCMEASUREWITHUNIT"
    }, "the factor of SQUAREDEGREE is typed as a length, but it has a unit of its own"
