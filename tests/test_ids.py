# This file was generated with the assistance of an AI coding tool.
"""The check of a file against IDS files: IfcTester's verdicts, read safely, in the shape of the other reports."""

from __future__ import annotations

import json
import socket
import subprocess  # nosec B404 - runs this package's own command
import sys
from pathlib import Path

import pytest

from ifc_lpg.files import check_ids
from ifc_lpg.ids import MAX_IDS_BYTES, CannotCheck, InvalidIds, ids_schema, read_ids, report_of
from tests.conftest import FIXTURES_DIR

MODEL = FIXTURES_DIR / "three_walls_gtin.ifc"
WALLS = FIXTURES_DIR / "walls.ids"
WALL_IDS = {"1Xezbq735AdBO_4DerG66G", "3k3LbXmTfDWODxdSuyCGYE"}

PROHIBITED = b"""<?xml version="1.0" encoding="UTF-8"?>
<ids xmlns="http://standards.buildingsmart.org/IDS" xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <info><title>No walls</title></info>
  <specifications>
    <specification name="Walls are prohibited" identifier="NW-1" ifcVersion="IFC2X3 IFC4">
      <applicability minOccurs="0" maxOccurs="0">
        <entity><name><simpleValue>IFCWALL</simpleValue></name></entity>
      </applicability>
    </specification>
  </specifications>
</ids>
"""


def _write(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def test_a_file_against_an_ids() -> None:
    report = report_of(MODEL, [WALLS])
    assert report["ruleset"].startswith("IfcTester 0.9") and report["ruleset"].endswith("IDS 1.0")
    by_name = {spec["name"]: spec for spec in report["specifications"]}
    assert (by_name["Walls carry a GTIN"]["status"], by_name["Walls carry a GTIN"]["applicable"]) == ("passed", 3)
    fire = by_name["Walls have a fire rating"]
    assert (fire["usage"], fire["status"], fire["applicable"], fire["issues"]) == ("optional", "failed", 3, 3)
    door = by_name["The model has a door"]
    assert (door["usage"], door["status"], door["applicable"], door["issues"]) == ("required", "failed", 0, 1)
    other = by_name["Only for IFC4X3"]
    assert other["for_this_schema"] is False and other["ifc_versions"] == ["IFC4X3_ADD2"]
    assert other["status"] == "passed", "as IfcTester decides by default, and Dataspatium with it"
    assert report["issue_count"] == report["error_count"] == len(report["issues"]) == 4
    assert not report["truncated"] and report["rules_not_run"] == []


def test_an_ids_with_a_restriction_is_read_and_run(tmp_path: Path) -> None:
    """A value may be an XML Schema restriction, whose base is a name with the document's prefix for XML Schema."""
    restricted = tmp_path / "restricted.ids"
    restricted.write_text(
        WALLS.read_text(encoding="utf-8").replace(
            "<baseName><simpleValue>GTIN</simpleValue></baseName>",
            '<baseName><simpleValue>GTIN</simpleValue></baseName><value><xs:restriction base="xs:string">'
            '<xs:pattern value="[0-9]+"/></xs:restriction></value>',
        ),
        encoding="utf-8",
    )
    assert "xs:restriction" in restricted.read_text(encoding="utf-8")
    read_ids(restricted.read_bytes())
    report = report_of(MODEL, [restricted])
    assert report["rules_not_run"] == []
    gtin = next(spec for spec in report["specifications"] if spec["name"] == "Walls carry a GTIN")
    assert (gtin["status"], gtin["applicable"]) == ("passed", 3), "the walls' GTINs 1, 2 and 3 are digits"


def test_every_issue_says_what_failed_and_where() -> None:
    issues = report_of(MODEL, [WALLS])["issues"]
    fire = [issue for issue in issues if issue["rule"] == "Walls have a fire rating"]
    assert len(fire) == 3 and all(issue["kind"] == "ids" and issue["severity"] == "error" for issue in fire)
    assert all("FireRating" in issue["message"] and "Pset_WallCommon" in issue["message"] for issue in fire)
    assert all(isinstance(issue["step_id"], int) and issue["ids"] == 0 for issue in fire)
    assert WALL_IDS <= {issue["global_id"] for issue in fire}
    (door,) = [issue for issue in issues if issue["rule"] == "The model has a door"]
    assert door["step_id"] is None and door["global_id"] is None and "required" in door["message"]


def test_a_prohibited_specification_lists_what_it_applies_to(tmp_path: Path) -> None:
    report = report_of(MODEL, [WALLS, _write(tmp_path, "no-walls.ids", PROHIBITED)])
    banned = [issue for issue in report["issues"] if issue["ids"] == 1]
    assert len(banned) == 3 and {issue["rule"] for issue in banned} == {"NW-1"}
    assert all("prohibited" in issue["message"] and issue["step_id"] for issue in banned)
    (spec,) = [spec for spec in report["specifications"] if spec["ids"] == 1]
    assert (spec["usage"], spec["status"], spec["title"], spec["identifier"]) == (
        "prohibited",
        "failed",
        "No walls",
        "NW-1",
    )


@pytest.mark.parametrize(
    ("data", "said"),
    [
        (b'<?xml version="1.0"?><!DOCTYPE ids [<!ENTITY x "y">]><ids>&x;</ids>', "DTD"),
        (b'<?xml version="1.0"?><!DOCTYPE ids [<!ENTITY x SYSTEM "file:///etc/passwd">]><ids>&x;</ids>', "DTD"),
        (b"<ids", "not XML"),
        (b'<ids xmlns="http://standards.buildingsmart.org/IDS"><info/></ids>', "not valid against IDS 1.0"),
        (b"<other/>", "not valid against IDS 1.0"),
    ],
    ids=["entity", "external entity", "not xml", "incomplete", "not ids"],
)
def test_an_ids_that_cannot_be_read_is_listed_and_not_run(tmp_path: Path, data: bytes, said: str) -> None:
    report = report_of(MODEL, [_write(tmp_path, "bad.ids", data), WALLS])
    (not_run,) = report["rules_not_run"]
    assert not_run["ids"] == 0 and not_run["rule"] == "IDS 1" and said in not_run["error"]
    assert "root:" not in json.dumps(report), "nothing of a file an entity points at comes back"
    assert {spec["ids"] for spec in report["specifications"]} == {1}, "the next IDS is still checked"


def test_an_ids_too_large_is_not_read() -> None:
    with pytest.raises(InvalidIds, match="larger than"):
        read_ids(b" " * (MAX_IDS_BYTES + 1))


def test_the_schema_is_built_without_the_network(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts: list[object] = []

    def refuse(*args: object, **kwargs: object) -> None:
        attempts.append(args[:2])
        raise OSError("no network in this test")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    ids_schema.cache_clear()
    try:
        read_ids(WALLS.read_bytes())
    finally:
        ids_schema.cache_clear()
    assert attempts == []


def test_a_file_that_cannot_be_opened_is_not_checked(tmp_path: Path) -> None:
    with pytest.raises(CannotCheck):
        report_of(_write(tmp_path, "broken.ifc", b"ISO-10303-21;\nHEADER;"), [WALLS])


def test_the_command_line_answers_in_json(tmp_path: Path) -> None:
    command = [sys.executable, "-m", "ifc_lpg.ids", str(MODEL), str(WALLS)]
    done = subprocess.run(command, capture_output=True, text=True, timeout=300, check=False)  # noqa: S603  # nosec B603
    assert done.returncode == 1, done.stderr
    assert json.loads(done.stdout)["issue_count"] == 4
    broken = _write(tmp_path, "broken.ifc", b"not a file")
    done = subprocess.run(  # noqa: S603  # nosec B603 - this package's own command
        [sys.executable, "-m", "ifc_lpg.ids", str(broken), str(WALLS)], capture_output=True, text=True, timeout=300
    )
    assert done.returncode == 2 and "error" in json.loads(done.stderr.strip().splitlines()[-1])


async def test_check_ids_runs_in_a_process_of_its_own() -> None:
    report = await check_ids(MODEL, [WALLS])
    assert report["issue_count"] == 4 and len(report["specifications"]) == 4
