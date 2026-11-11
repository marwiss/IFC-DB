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
    assert answer["length_unit"][1] == "METER" and answer["check"] == "passed"
    written = StepFile(target.read_bytes())
    factors = {step: parameters for step, keyword, parameters in written if keyword == b"IFCMEASUREWITHUNIT"}
    original = StepFile((FIXTURES_DIR / "three_walls_gtin.ifc").read_bytes())
    assert factors == {
        step: parameters for step, keyword, parameters in original if keyword == b"IFCMEASUREWITHUNIT"
    }, "the factor of SQUAREDEGREE is typed as a length, but it has a unit of its own"


WINDOW_STYLE = FIXTURES_DIR / "window_style.ifc"
CHOICES = {"IfcWindowStyle": "type", "space_boundary_without_element": "virtual", "owner_history_dates": "dated"}


def test_what_cannot_be_converted_is_listed_at_once_with_its_options(tmp_path: Path) -> None:
    pytest.importorskip("ifcopenshell", reason="IfcOpenShell is not installed")
    target = tmp_path / "out.ifc"
    answer = prepare_file(WINDOW_STYLE, target, schema="IFC4X3_ADD2", length_unit=None)
    found = {problem["key"]: (problem["options"], problem.get("later", False)) for problem in answer["problems"]}
    assert found == {
        "space_boundary_without_element": (["virtual", "leave_out"], False),
        "owner_history_dates": (["dated"], False),
        "IfcWindowStyle": (["type", "proxy", "leave_out"], True),
    }, "the style that IFC4 keeps and IFC4X3 does not is there in the first round"
    assert not target.exists(), "nothing is written before the choices"
    [boundary] = [p for p in answer["problems"] if p["key"] == "space_boundary_without_element"]
    assert boundary["affects"] == ["0Window000000000000022"] and not boundary["affects_cut"]
    with pytest.raises(ValueError, match="no option"):
        prepare_file(
            WINDOW_STYLE,
            target,
            schema="IFC4X3_ADD2",
            length_unit=None,
            choices={**CHOICES, "IfcWindowStyle": "polyline"},
        )


def test_the_choices_are_made_and_the_conversion_is_checked(tmp_path: Path) -> None:
    pytest.importorskip("ifcopenshell", reason="IfcOpenShell is not installed")
    target = tmp_path / "out.ifc"
    answer = prepare_file(WINDOW_STYLE, target, schema="IFC4X3_ADD2", length_unit=None, choices=CHOICES)
    assert (answer["schema"], answer["check"]) == (["IFC2X3", "IFC4X3_ADD2"], "passed")
    written = {keyword: parameters for _step, keyword, parameters in StepFile(target.read_bytes())}
    assert b"IFCWINDOWSTYLE" not in written and written[b"IFCWINDOWTYPE"][10] == b".SINGLE_PANEL."
    assert b"IFCVIRTUALELEMENT" in written, "the virtual boundary has a virtual element"
    left = prepare_file(
        WINDOW_STYLE,
        tmp_path / "left.ifc",
        schema="IFC4X3_ADD2",
        length_unit=None,
        choices={**CHOICES, "IfcWindowStyle": "leave_out", "space_boundary_without_element": "leave_out"},
    )
    kept = {keyword for _step, keyword, _p in StepFile((tmp_path / "left.ifc").read_bytes())}
    assert left["check"] == "passed" and not kept & {b"IFCWINDOWTYPE", b"IFCRELSPACEBOUNDARY", b"IFCRELDEFINESBYTYPE"}


def test_an_issue_the_file_did_not_have_stops_the_conversion(tmp_path: Path) -> None:
    pytest.importorskip("ifcopenshell", reason="IfcOpenShell is not installed")
    from ifc_lpg.prepare import _check

    broken = tmp_path / "broken.ifc"
    broken.write_bytes(WINDOW_STYLE.read_bytes().replace(b"$,$,1.2,1.);", b"$,$,-1.2,1.);"))
    [issue] = _check(WINDOW_STYLE, broken, {})
    assert (issue["instance"], issue["severity"]) == (30, "error") and set(issue) == {
        "kind",
        "severity",
        "message",
        "instance",
        "attribute",
    }, "in the form of a validation report"
