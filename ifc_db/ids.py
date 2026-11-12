# This file was generated with the assistance of an AI coding tool.
"""A file against Information Delivery Specifications: ``python -m ifc_db.ids FILE IDS [IDS ...]``.

ADR 0013. IfcOpenShell's IfcTester checks the file against each IDS, with its own defaults, so that a server and
Dataspatium, which runs IfcTester in the browser, give the same verdicts. An IDS comes from a person, so it is read here
and not by IfcTester: with defusedxml, which refuses a DTD and with it every entity, and against IDS 1.0's schema as
IfcTester ships it, built with xmlschema's access limited to local files. That schema imports three others by their
addresses at w3.org, which xmlschema would otherwise fetch over plain HTTP, waiting 300 s for each without a network.

Every requirement an element fails is an issue with the severity error, and so is a required specification that applies
to nothing, and an element that a prohibited one applies to. An IDS that cannot be read is listed apart, as not run. The
answer is JSON on stdout in the shape of ``validation.py``'s report, with a summary of each specification.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from collections.abc import Iterator
from functools import cache
from pathlib import Path
from typing import Any

from ifc_db.validation import MAX_MESSAGE, MAX_REPORTED
from ifc_db.worker import keep_stdout_for_the_answer, limit_memory

IDS_NAMESPACE = "http://standards.buildingsmart.org/IDS"
MAX_IDS_BYTES = 20 * 1024 * 1024
MAX_RULE = 200


class CannotCheck(RuntimeError):
    """The file could not be opened, so nothing is known of what the specifications would find."""


class InvalidIds(ValueError):
    """An IDS that cannot be read: too large, not XML, with a DTD, or not valid against IDS 1.0."""


@cache
def ids_schema() -> Any:
    """IDS 1.0's schema as IfcTester ships it, built without the network."""
    from ifctester import ids as ifctester_ids
    from xmlschema import XMLSchema

    schema = XMLSchema(str(Path(ifctester_ids.__file__).with_name("ids.xsd")), allow="local")
    # IfcTester builds its own copy on first use, which reaches for w3.org; it finds this one instead.
    ifctester_ids.schema = schema
    return schema


def read_ids(data: bytes) -> Any:
    """An IDS as IfcTester's ``Ids``, read safely. Raises :class:`InvalidIds`."""
    import defusedxml.ElementTree as SafeElementTree
    from defusedxml import DefusedXmlException
    from ifctester import ids as ifctester_ids

    if len(data) > MAX_IDS_BYTES:
        raise InvalidIds(f"larger than {MAX_IDS_BYTES // (1024 * 1024)} MB")
    try:
        root = SafeElementTree.fromstring(data, forbid_dtd=True)
        # An element keeps no prefixes, but an IDS has them in its values too (base="xs:string"), so the check is
        # given the document's own declarations.
        declared = {
            prefix: uri
            for _event, (prefix, uri) in SafeElementTree.iterparse(
                io.BytesIO(data), events=("start-ns",), forbid_dtd=True
            )
        }
    except DefusedXmlException as exc:
        raise InvalidIds(f"it has a DTD or an entity, which an IDS has no use for ({type(exc).__name__})") from None
    except SafeElementTree.ParseError as exc:
        raise InvalidIds(f"not XML: {exc}"[:MAX_MESSAGE]) from None
    schema = ids_schema()
    namespaces = {**declared, "": IDS_NAMESPACE}
    error = next(iter(schema.iter_errors(root, namespaces=namespaces)), None)
    if error is not None:
        raise InvalidIds(f"not valid against IDS 1.0: {getattr(error, 'reason', None) or error}"[:MAX_MESSAGE])
    try:
        decoded = schema.decode(root, strip_namespaces=True, namespaces=namespaces)
        requirements = ifctester_ids.Ids().parse(decoded)
        # IfcTester 0.9.0 leaves out the identifier IDS 1.0 gives a specification; it is read here, in the same order.
        listed = decoded["specifications"]["specification"]
        listed = listed if isinstance(listed, list) else [listed]
        for specification, entry in zip(requirements.specifications, listed, strict=True):
            specification.identifier = entry.get("@identifier")
        return requirements
    except Exception as exc:  # a valid IDS that IfcTester cannot take apart is as unread as an invalid one
        raise InvalidIds(f"IfcTester could not read it: {type(exc).__name__}: {exc}"[:MAX_MESSAGE]) from None


def _described(facet: Any, clause: str, specification: Any) -> str:
    try:
        if clause == "requirement":
            return str(facet.to_string("requirement", specification, facet))
        return str(facet.to_string("applicability"))
    except Exception:  # a facet IfcTester cannot put in words is still named by its kind
        return type(facet).__name__


def _issue(specification: Any, index: int, message: str, element: Any = None) -> dict[str, Any]:
    global_id = getattr(element, "GlobalId", None) if element is not None else None
    return {
        "kind": "ids",
        "rule": str(specification.identifier or specification.name or "")[:MAX_RULE],
        "severity": "error",
        "message": f"{specification.name}: {message}"[:MAX_MESSAGE],
        "step_id": element.id() if element is not None else None,
        "global_id": global_id if isinstance(global_id, str) else None,
        "attribute": None,
        "ids": index,
    }


def _issues_of(specification: Any, index: int) -> Iterator[dict[str, Any]]:
    usage = specification.get_usage()
    applicability = "; ".join(
        _described(facet, "applicability", specification) for facet in specification.applicability
    )
    if usage == "prohibited":
        for element in specification.applicable_entities:
            yield _issue(specification, index, f"prohibited: {applicability}", element)
        return
    if usage == "required" and not specification.applicable_entities:
        yield _issue(specification, index, f"required, but nothing in the file is this: {applicability}")
    for facet in specification.requirements:
        described = _described(facet, "requirement", specification)
        for failure in facet.failures:
            yield _issue(specification, index, f"{described}. {failure['reason']}", failure["element"])


def _summary_of(specification: Any, index: int, title: str, issues: int) -> dict[str, Any]:
    usage = specification.get_usage()
    applicable = len(specification.applicable_entities)
    if usage == "optional" and applicable == 0:
        status = "not applicable"
    else:
        status = "passed" if specification.status else "failed"
    versions = specification.ifcVersion
    return {
        "ids": index,
        "title": title,
        "name": str(specification.name or ""),
        "identifier": specification.identifier,
        "usage": usage,
        "ifc_versions": list(versions) if isinstance(versions, list | tuple) else str(versions).split(),
        "for_this_schema": bool(specification.is_ifc_version),
        "status": status,
        "applicable": applicable,
        "issues": issues,
    }


def report_of(path: Path, sources: list[Path]) -> dict[str, Any]:
    """The check of a file against the IDS files ``sources``, in the shape of ``validation.report_of``."""
    try:
        import ifcopenshell
        import ifctester

        model = ifcopenshell.open(str(path))
    except Exception as exc:  # IfcOpenShell's parser raises more than one kind for a file it cannot open
        raise CannotCheck(f"{type(exc).__name__}: {str(exc)[:300]}") from exc
    issues: list[dict[str, Any]] = []
    issue_count = 0
    specifications: list[dict[str, Any]] = []
    not_run: list[dict[str, Any]] = []
    for index, source in enumerate(sources):
        try:
            requirements = read_ids(source.read_bytes())
            requirements.validate(model)
        except (InvalidIds, OSError) as exc:
            not_run.append({"rule": f"IDS {index + 1}", "ids": index, "error": str(exc)[:MAX_MESSAGE]})
            continue
        except Exception as exc:  # a specification IfcTester fails on says nothing of the file
            error = f"IfcTester failed: {type(exc).__name__}: {exc}"[:MAX_MESSAGE]
            not_run.append({"rule": f"IDS {index + 1}", "ids": index, "error": error})
            continue
        title = str(requirements.info.get("title") or "")
        for specification in requirements.specifications:
            found = 0
            for issue in _issues_of(specification, index):
                found += 1
                if len(issues) < MAX_REPORTED:
                    issues.append(issue)
            issue_count += found
            specifications.append(_summary_of(specification, index, title, found))
    return {
        "ruleset": f"IfcTester {ifctester.__version__}: IDS 1.0",
        "issue_count": issue_count,
        "error_count": issue_count,
        "truncated": issue_count > MAX_REPORTED,
        "issues": issues,
        "rules_not_run": not_run,
        "specifications": specifications,
    }


def main(argv: list[str] | None = None) -> int:
    """Exit 0 when nothing failed, 1 when a specification failed or an IDS could not be read, 2 for a file that could
    not be checked. The answer is JSON on stdout."""
    parser = argparse.ArgumentParser(prog="ifc-db-ids")
    parser.add_argument("file", type=Path)
    parser.add_argument("ids", type=Path, nargs="+")
    parser.add_argument("--memory-mb", type=int, default=0)
    args = parser.parse_args(argv)
    if args.memory_mb:
        limit_memory(args.memory_mb)
    try:
        report = report_of(args.file, args.ids)
    except CannotCheck as exc:
        sys.stderr.write(json.dumps({"error": str(exc)}) + "\n")
        return 2
    sys.stdout.write(json.dumps(report) + "\n")
    sys.stdout.flush()
    return 1 if report["issue_count"] or report["rules_not_run"] else 0


if __name__ == "__main__":
    keep_stdout_for_the_answer()
    sys.exit(main())
