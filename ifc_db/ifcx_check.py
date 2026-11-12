# This file was generated with the assistance of an AI coding tool.
"""Whether the attributes of IFC5 files keep to their schemas: ``python -m ifc_db.ifcx_check FILE... [--schemas DIR]``.

Experimental, as IFC5 is. An IFCX file says what its attributes are in ``schemas``, and imports more schemas by URI. The
files are composed as for the graph, and every attribute is checked against the schema for its key, the files' own over
the imported ones: its data type (``String``, ``Real``, ``Integer``, ``Boolean``, ``DateTime``, ``Enum``, ``Array``,
``Object``, ``Reference``), the options of an enumeration, the type of each member of an array, and the members of an
object, of which only those marked ``optional`` may be missing. The imported schemas are not fetched while a file is
checked: ``python -m ifc_db.ifcx_check --fetch DIR`` fetches the ones pinned here, each checked against its SHA-256,
into a directory that the check then reads. An import that is not pinned is a warning, and so is an attribute without a
schema while such an import is there to say what it is. The answer is JSON on stdout, with issues in the form of a
validation report, ``kind`` ``ifcx-schema``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from ifc_db.files import DEFAULT_LIMITS, Limits, run_worker

MAX_REPORTED = 10_000
MANIFEST = "manifest.json"
# The schemas buildingSMART's IFC5 examples import, as fetched on 2026-11-10.
PINNED = {
    "https://ifcx.dev/@standards.buildingsmart.org/ifc/core/ifc@v5a.ifcx": (
        "7ada4aea92eefd97e1bc0e4f18bc280ec869cfe7a443250da8f0e4b429cf047a"
    ),
    "https://ifcx.dev/@standards.buildingsmart.org/ifc/core/prop@v5a.ifcx": (
        "5bd444faf1b515b20f441b4293bdcc9874a888618017d3822d94b15a794d96c2"
    ),
    "https://ifcx.dev/@standards.buildingsmart.org/ifc/ifc-mat/ifc-mat@v1.0.0.ifcx": (
        "0bfecb9f32ce8f104cc10333c67ecef14307898d5dea054bbbdfa8569f85f430"
    ),
    "https://ifcx.dev/@openusd.org/usd@v1.ifcx": "ec9d50cd00e20c8b7fefd0eca1ca0246d2153c93739f6b4e62721f775e58b3cc",
    "https://ifcx.dev/@nlsfb/nlsfb@v1.ifcx": "22c3f74fa121296e5b28bf5e5282ea91fea956c29681992eb78f3a1d0b3899f2",
}


def fetch_schemas(target: Path) -> list[str]:
    """Fetch the pinned schemas into ``target``, each refused unless its SHA-256 is the pinned one. Returns the URIs."""
    from ifc_db.gherkin_install import fetch
    from ifc_db.ifcx import read_layer

    target.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for uri, sha256 in sorted(PINNED.items()):
        data = fetch(uri, sha256)
        read_layer(data)  # it is an IFCX file, or it is refused here
        (target / f"{sha256}.ifcx").write_bytes(data)
        manifest[uri] = f"{sha256}.ifcx"
    (target / MANIFEST).write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    return sorted(manifest)


def read_schemas(folder: Path) -> dict[str, dict[str, Any]]:
    """The schemas of each pinned URI fetched into ``folder``, read again and checked against their pins."""
    from ifc_db.ifcx import read_layer

    found = {}
    for uri, name in json.loads((folder / MANIFEST).read_text(encoding="utf-8")).items():
        data = (folder / name).read_bytes()
        if PINNED.get(uri) != hashlib.sha256(data).hexdigest():
            raise ValueError(f"{name} is not the pinned schema of {uri}")
        found[uri] = read_layer(data)["schemas"]
    return found


def _number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def problems(value: Any, spec: dict[str, Any], trail: str) -> list[str]:
    """What is wrong with ``value`` against a schema's ``value``, each said with where it is."""
    kind = spec.get("dataType")
    simple = {
        "String": isinstance(value, str),
        "DateTime": isinstance(value, str),
        "Real": _number(value),
        "Integer": isinstance(value, int) and not isinstance(value, bool),
        "Boolean": isinstance(value, bool),
    }
    if kind in simple:
        return [] if simple[kind] else [f"{trail} is {json.dumps(value)[:80]}, not a {kind}"]
    if kind == "Enum":
        options = (spec.get("enumRestrictions") or {}).get("options") or []
        return (
            []
            if value in options
            else [f"{trail} is {json.dumps(value)[:80]}, not one of {', '.join(map(str, options))}"]
        )
    if kind == "Reference":
        return [] if isinstance(value, dict) and isinstance(value.get("ref"), str) else [f"{trail} is no reference"]
    if kind == "Array":
        if not isinstance(value, list):
            return [f"{trail} is no array"]
        inner = (spec.get("arrayRestrictions") or {}).get("value")
        found = []
        for index, item in enumerate(value):
            found += problems(item, inner, f"{trail}[{index}]") if isinstance(inner, dict) else []
            if len(found) > 10:
                break
        return found
    if kind == "Object":
        if not isinstance(value, dict):
            return [f"{trail} is no object"]
        members = (spec.get("objectRestrictions") or {}).get("values") or {}
        found = [
            f"{trail}.{name} is not there"
            for name, inner in members.items()
            if name not in value and not inner.get("optional")
        ]
        found += [f"{trail}.{name} is no member the schema has" for name in value if name not in members]
        for name, inner in members.items():
            if name in value and value[name] is not None and isinstance(inner, dict):
                found += problems(value[name], inner, f"{trail}.{name}")
        return found
    return [f"{trail} has a schema of the data type {kind!r}, which this check does not know"]


def check(composed: Any, imported: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Every attribute of a composition against its schema, the files' own over those of the imports in
    ``imported`` (URI to schemas)."""
    schemas: dict[str, Any] = {}
    unknown = []
    for item in composed.imports:
        uri = item.get("uri") if isinstance(item, dict) else None
        if uri in imported:
            schemas.update(imported[uri])
        elif uri not in unknown:
            unknown.append(uri)
    schemas.update(composed.schemas)
    issues = [
        _issue("warning", f"the import {uri} is not pinned, so its schemas are unknown", None, None) for uri in unknown
    ]
    for path, parts in sorted(composed.objects.items()):
        for key, value in sorted((parts.get("attributes") or {}).items()):
            if value is None:
                continue
            spec = schemas.get(key)
            if not isinstance(spec, dict) or not isinstance(spec.get("value"), dict):
                severity = "warning" if unknown else "error"
                issues.append(_issue(severity, f"no schema says what {key} is", path, key))
                continue
            issues += [_issue("error", problem, path, key) for problem in problems(value, spec["value"], key)]
    return issues


def _issue(severity: str, message: str, path: str | None, attribute: str | None) -> dict[str, Any]:
    return {
        "kind": "ifcx-schema",
        "severity": severity,
        "message": message[:1000],
        "path": path,
        "attribute": attribute,
    }


def report(sources: list[Path], schemas: Path | None) -> dict[str, Any]:
    """The check of the files ``sources``, composed, with the schemas fetched into ``schemas``."""
    from ifc_db.ifcx import MAX_BYTES, IfcxError, compose, read_layer

    layers = []
    for source in sources:
        if source.stat().st_size > MAX_BYTES:
            raise IfcxError(f"{source.name} is larger than {MAX_BYTES} bytes")
        layers.append((source.name, read_layer(source.read_bytes())))
    issues = check(compose(layers), read_schemas(schemas) if schemas else {})
    return {
        "ruleset": "IFC-DB's check of IFCX attributes against their schemas",
        "issue_count": len(issues),
        "error_count": sum(1 for issue in issues if issue["severity"] == "error"),
        "truncated": len(issues) > MAX_REPORTED,
        "issues": issues[:MAX_REPORTED],
    }


async def check_ifcx(
    paths: list[Path], *, schemas: Path | None = None, limits: Limits = DEFAULT_LIMITS
) -> dict[str, Any]:
    """The check of IFCX files, composed, in the worker; ``schemas`` is where :func:`fetch_schemas` put the imports."""
    command = [sys.executable, "-m", "ifc_db.ifcx_check", "--memory-mb", str(limits.memory_mb)]
    command += ["--schemas", str(schemas)] if schemas else []
    answer = await run_worker(
        [*command, "--", *map(str, paths)], "the IFCX check", limits.timeout_seconds, answers=(0, 1)
    )
    return json.loads(answer.strip().splitlines()[-1])


def main(argv: list[str] | None = None) -> int:
    """Exit 0 when nothing is wrong, 1 when something is, and 3 for what could not be checked."""
    parser = argparse.ArgumentParser(prog="ifc-db-ifcx-check")
    parser.add_argument("files", type=Path, nargs="*")
    parser.add_argument("--schemas", type=Path, help="where --fetch put the pinned schemas")
    parser.add_argument("--fetch", type=Path, help="fetch the pinned schemas into this directory, and check nothing")
    parser.add_argument("--memory-mb", type=int, default=0)
    args = parser.parse_args(argv)
    from ifc_db.worker import limit_memory

    if args.memory_mb:
        limit_memory(args.memory_mb)
    try:
        if args.fetch:
            sys.stdout.write(json.dumps({"fetched": fetch_schemas(args.fetch)}) + "\n")
            return 0
        if not args.files:
            raise ValueError("no file to check")
        answer = report(args.files, args.schemas)
    except (ValueError, KeyError, OSError, RuntimeError) as exc:
        sys.stderr.write(json.dumps({"error": f"{type(exc).__name__}: {str(exc)[:500]}"}) + "\n")
        return 3
    sys.stdout.write(json.dumps(answer) + "\n")
    return 1 if answer["error_count"] else 0


if __name__ == "__main__":
    sys.exit(main())
