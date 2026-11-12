# This file was generated with the assistance of an AI coding tool.
"""Grafeo's HTTP transport without a server: values both ways, refusals, where credentials may go, and parts."""

from __future__ import annotations

import decimal
import json
from typing import Any

import pytest

from ifc_db.backends.grafeo import ConstraintViolation, GrafeoBackend, GrafeoError, Native, plain, rows_of, tagged

SECRET = "s3cret-token-value"


def test_every_value_goes_tagged_as_grafeo_reads_a_parameter() -> None:
    value = {"n": None, "b": True, "i": -(2**63), "f": -0.0, "s": "å", "l": [1, [2.5]], "m": {"k": False}}
    assert tagged(value) == {
        "Map": {
            "n": "Null",
            "b": {"Bool": True},
            "i": {"Int64": -(2**63)},
            "f": {"Float64": -0.0},
            "s": {"String": "å"},
            "l": {"List": [{"Int64": 1}, {"List": [{"Float64": 2.5}]}]},
            "m": {"Map": {"k": {"Bool": False}}},
        }
    }
    with pytest.raises(TypeError, match="bytes"):
        tagged(b"x")


def test_an_answer_is_read_as_python_has_it_and_a_decimal_stays_a_decimal() -> None:
    answer = {"columns": ["v", "d", "t"], "rows": [[{"a": [1.0, -0.0, 5e-324]}, {"$decimal": "1.5"}, {"$date": "x"}]]}
    (row,) = rows_of(200, json.dumps(answer).encode())
    assert row["v"] == {"a": [1.0, -0.0, 5e-324]} and isinstance(row["v"]["a"][0], float)
    assert row["d"] == decimal.Decimal("1.5") and row["t"] == Native("date", "x")
    assert plain({"a": 1, "b": 2}) == {"a": 1, "b": 2}


def test_a_refused_constraint_is_told_from_other_refusals() -> None:
    unique = b'{"error":"bad_request","detail":"GRAFEO-V001: Invalid value: Constraint violation: UNIQUE constraint"}'
    with pytest.raises(ConstraintViolation) as refused:
        rows_of(400, unique)
    assert (refused.value.status, refused.value.code) == (400, "GRAFEO-V001")
    with pytest.raises(GrafeoError) as syntax:
        rows_of(400, b'{"error":"bad_request","detail":"GRAFEO-Q001: syntax error"}')
    assert not isinstance(syntax.value, ConstraintViolation) and syntax.value.code == "GRAFEO-Q001"
    with pytest.raises(GrafeoError, match="max-body-size") as large:
        rows_of(413, b"Failed to buffer the request body: length limit exceeded")
    assert large.value.status == 413 and large.value.code is None


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1:7474", "http://localhost:7474", "http://[::1]:7474", "https://db.example"]
)
def test_credentials_go_over_https_or_to_this_machine(url: str) -> None:
    backend = GrafeoBackend(url, token=SECRET)
    assert SECRET not in repr(backend) and SECRET not in repr(vars(backend))


@pytest.mark.parametrize(
    ("url", "options", "why"),
    [
        ("http://192.0.2.1:7474", {"token": SECRET}, "only over HTTPS"),
        ("http://db.example:7474", {"user": "u", "password": SECRET}, "only over HTTPS"),
        (f"https://u:{SECRET}@db.example", {}, "not in the address"),
        ("https://db.example", {"user": "u"}, "needs a password"),
        ("https://db.example", {"token": SECRET, "user": "u", "password": "p"}, "not both"),
        ("http://127.0.0.1:7474", {"ca_file": "ca.pem"}, "https:// address"),
        ("bolt://127.0.0.1:7687", {}, "not an http"),
    ],
)
def test_an_address_that_would_put_credentials_at_risk_is_refused(url: str, options: dict[str, Any], why: str) -> None:
    with pytest.raises(ValueError, match=why) as refused:
        GrafeoBackend(url, **options)
    assert SECRET not in str(refused.value)


async def test_rows_go_in_parts_no_larger_than_the_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = GrafeoBackend("http://127.0.0.1:7474", max_body=600)
    bodies: list[bytes] = []

    def post(body: bytes) -> tuple[int, bytes]:
        bodies.append(body)
        return 200, b'{"columns": [], "rows": []}'

    monkeypatch.setattr(backend._http, "_post", post)
    rows = [{"i": i, "text": "x" * (900 if i == 7 else 40)} for i in range(20)]
    await backend._each("FOR row IN $rows INSERT (:T {i: row.i})", rows, model_id="m")
    sent = [json.loads(body) for body in bodies]
    assert [row["Map"]["i"]["Int64"] for body in sent for row in body["params"]["rows"]["List"]] == list(range(20))
    assert all(len(body) <= 600 or len(json.loads(body)["params"]["rows"]["List"]) == 1 for body in bodies)
    assert len(bodies) > 2 and all(body["params"]["model_id"] == {"String": "m"} for body in sent)
    assert all(body["language"] == "gql" and body["query"].startswith("FOR row IN $rows") for body in sent)
