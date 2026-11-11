# This file was generated with the assistance of an AI coding tool.
"""Make a model ready to be merged into another: its schema migrated and its lengths in the other's unit.

``python -m ifc_lpg.prepare IFC OUT [--schema SCHEMA] [--length-unit UNIT] [--memory-mb N]`` needs the extra
``ifcopenshell`` and runs in a process of its own. It migrates the file to ``SCHEMA`` with IfcOpenShell's ``Migrator``,
which does its best: a class the schema does not have becomes a proxy, and the answer lists each such instance. It turns
every length into ``UNIT`` (``METER``, ``MILLIMETER``, ``FOOT`` and so on) with ``convert_file_length_units``; other units
are not converted. IfcOpenShell reads and writes the file here, which changes a few values on the way, such as
``.NULLENUM.``; the result is a new model, not the same file. The answer is JSON on stdout.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from pathlib import Path
from typing import Any

from ifc_lpg.step import StepError, StepFile
from ifc_lpg.worker import keep_stdout_for_the_answer, limit_memory

MAX_LISTED = 100


def prepare_file(source: Path, target: Path, *, schema: str | None, length_unit: str | None) -> dict[str, Any]:
    """Write ``source`` migrated to ``schema`` and with its lengths in ``length_unit`` to ``target``. Either can be
    left out. Returns what was done, with the instances whose class changed."""
    import ifcopenshell
    import ifcopenshell.util.schema
    import ifcopenshell.util.unit

    instances = len(StepFile(source.read_bytes()))  # the file is whole, or it is refused here
    model = ifcopenshell.open(str(source))
    if sum(1 for _ in model) != instances:
        raise ValueError(f"IfcOpenShell read {sum(1 for _ in model)} of the file's {instances} instances")
    answer: dict[str, Any] = {"schema": [model.schema_identifier, model.schema_identifier], "changed": []}
    if schema and schema.upper() != model.schema_identifier.upper():
        wanted: Any = schema  # a name such as IFC4, which the stubs know as a literal
        migrated = ifcopenshell.file(schema=wanted)
        migrator = ifcopenshell.util.schema.Migrator(fallback_element_to_proxy=True)
        with contextlib.redirect_stdout(sys.stderr):  # the migrator prints what it cannot carry over
            migrator.preprocess(model, migrated)
            for element in model:
                migrator.migrate(element, migrated)
        for old, new in sorted(migrator.migrated_ids.items()):
            before, after = model.by_id(old), migrated.by_id(new)
            if before.is_a() != after.is_a():
                global_id = before.GlobalId if before.is_a("IfcRoot") else None
                answer["changed"].append(
                    {"step": old, "global_id": global_id, "from": before.is_a(), "to": after.is_a()}
                )
        answer["schema"][1] = migrated.schema_identifier
        model = migrated
    if length_unit:
        before = ifcopenshell.util.unit.get_project_unit(model, "LENGTHUNIT")
        # A value with a unit of its own, such as the factor of a conversion-based unit, is not in the project's unit,
        # but convert_file_length_units turns it if it is typed as a length; it is put back as it was.
        own = {measure.id(): measure.ValueComponent for measure in model.by_type("IfcMeasureWithUnit")}
        own = {step: (value.is_a(), value.wrappedValue) for step, value in own.items() if value is not None}
        model = ifcopenshell.util.unit.convert_file_length_units(model, length_unit)
        for step, (kind, value) in own.items():
            model.by_id(step).ValueComponent = model.create_entity(kind, value)
        answer["length_unit"] = [ifcopenshell.util.unit.get_full_unit_name(before) if before else None, length_unit]
    model.write(str(target))
    answer["changed_count"] = len(answer["changed"])
    answer["changed"] = answer["changed"][:MAX_LISTED]
    return answer


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ifc-lpg-prepare")
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument("--schema")
    parser.add_argument("--length-unit")
    parser.add_argument("--memory-mb", type=int, default=0)
    args = parser.parse_args(argv)
    if args.memory_mb:
        limit_memory(args.memory_mb)
    try:
        answer = prepare_file(args.source, args.target, schema=args.schema, length_unit=args.length_unit)
    except (StepError, ValueError, RuntimeError, OSError) as exc:
        sys.stderr.write(json.dumps({"error": f"{type(exc).__name__}: {str(exc)[:500]}"}) + "\n")
        return 3
    sys.stdout.write(json.dumps(answer) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    keep_stdout_for_the_answer()
    sys.exit(main())
