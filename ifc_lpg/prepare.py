# This file was generated with the assistance of an AI coding tool.
"""Make a model ready to be merged into another: its schema migrated, its lengths in the other's unit, and checked.

``python -m ifc_lpg.prepare IFC OUT [--schema SCHEMA] [--length-unit UNIT] [--resolve KEY=OPTION...] [--memory-mb N]``
needs the extra ``ifcopenshell`` and runs in a process of its own. It migrates the file to ``SCHEMA`` with IfcOpenShell's
``Migrator``, through IFC4 between IFC2X3 and IFC4X3, and turns every length into ``UNIT`` (``METER``, ``MILLIMETER``,
``FOOT`` and so on) with ``convert_file_length_units``; other units are not converted.

What cannot be migrated is not dropped or changed without a choice (F32): the answer lists each class the migrator has
no equivalent of, with the options there are, and the user chooses. ``--resolve`` gives a choice per class, or for
``owner_history``: ``type`` makes an IfcWindowStyle or IfcDoorStyle an IfcWindowType or IfcDoorType, ``proxy`` makes an
element or a type a proxy, ``leave_out`` takes the objects out with their relationships, ``polyline`` makes an
IfcIndexedPolyCurve of straight segments an IfcPolyline, ``owner`` gives what IFC2X3 needs an owner history for, and
has none, one made by IFC-LPG, and ``virtual`` gives a space boundary without a building element, which IFC4 needs one
on, an IfcVirtualElement, as IFC4 means virtual boundaries to have, ``dated`` gives an owner history that says
what changed, and IFC4 needs a date of change on, its date of creation, and ``assignment`` puts the styles of a styled
item in an IfcPresentationStyleAssignment, which IFC2X3 needs them in. What a later step of a migration through IFC4
cannot carry is listed at once, so that one round of choices does. The migrator's own way out, a made-up owner history
for each instance, is not taken.

Then the conversion is checked (F33 a): the file and the result are validated, the file's issues are carried over to the
result's numbering, by instance and attribute, and an issue that the file did not have stops it. IfcOpenShell reads and
writes the file here, which changes a few values on the way, such as ``.NULLENUM.``; the result is a new model, not the
same file. The answer is JSON on stdout; the exit code is 0 when done, 1 when choices are needed, and 2 when the check
found new issues.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import time
from pathlib import Path
from typing import Any

from ifc_lpg.step import StepError, StepFile
from ifc_lpg.worker import keep_stdout_for_the_answer, limit_memory

RESOLUTIONS = ("type", "proxy", "leave_out", "polyline", "owner", "virtual", "dated", "assignment")
OWNER = "owner_history"
BOUNDARY = "space_boundary_without_element"
DATES = "owner_history_dates"
STYLES = "styled_item_styles"
MAX_LISTED = 100
_STYLES = {"IfcWindowStyle": "IfcWindowType", "IfcDoorStyle": "IfcDoorType"}


def _family(schema: str) -> str:
    name = schema.upper()
    return "IFC2X3" if name.startswith("IFC2X3") else "IFC4X3" if name.startswith("IFC4X3") else "IFC4"


def _capped(items: list[Any]) -> tuple[list[Any], bool]:
    return items[:MAX_LISTED], len(items) > MAX_LISTED


def _products_of(element: Any, depth: int = 0) -> set[str]:
    """The GlobalIds of the products an instance belongs to, up through what refers to it."""
    if element.is_a("IfcRoot"):
        return {element.GlobalId}
    if depth > 8:
        return set()
    found: set[str] = set()
    for referrer in element.file.get_inverse(element):
        found |= _products_of(referrer, depth + 1)
        if len(found) > MAX_LISTED:
            break
    return found


def _straight(curve: Any) -> bool:
    return curve.Segments is None or all(segment.is_a("IfcLineIndex") for segment in curve.Segments)


def _wanted(schema: str) -> Any:
    return schema  # a name such as IFC4, which the stubs know as a literal


def _options(name: str, elements: list[Any]) -> list[str]:
    """What can be done about the instances of a class that a schema has no equivalent of."""
    options = ["type"] if name in _STYLES else []
    if all(e.is_a("IfcElement") or e.is_a("IfcTypeProduct") for e in elements):
        options.append("proxy")
    if all(e.is_a("IfcProduct") or e.is_a("IfcTypeProduct") or e.is_a("IfcRelationship") for e in elements):
        options.append("leave_out")
    if name == "IfcIndexedPolyCurve" and all(map(_straight, elements)):
        options.append("polyline")
    return options


def _problems(model: Any, schema: str) -> dict[str, dict[str, Any]]:
    """What of ``model`` the migrator cannot carry over to ``schema``, by class, with the options for each."""
    import ifcopenshell
    import ifcopenshell.util.schema

    scratch = ifcopenshell.file(schema=_wanted(schema))
    migrator = ifcopenshell.util.schema.Migrator()
    by_class: dict[str, list[Any]] = {}
    for element in model:
        try:
            with contextlib.redirect_stdout(sys.stderr):
                migrator.migrate_class(element, scratch)
        except Exception:  # the migrator says so with NotImplementedError and other errors
            by_class.setdefault(element.is_a(), []).append(element)
    curves = {e.id() for e in by_class.get("IfcIndexedPolyCurve", ()) if _straight(e)}
    for name in [n for n in by_class if n.startswith("IfcCartesianPointList")]:
        # A point list that only a curve becoming a polyline holds goes with it, and is no problem of its own.
        by_class[name] = [e for e in by_class[name] if not {r.id() for r in model.get_inverse(e)} <= curves]
    return {
        name: _problem(name, "no_equivalent", elements, _options(name, elements), f"{schema} has no {name}")
        for name, elements in sorted(by_class.items())
        if elements
    }


def _undated(model: Any) -> list[Any]:
    return [
        history
        for history in model.by_type("IfcOwnerHistory")
        if history.ChangeAction not in (None, "NOCHANGE", "NOTDEFINED") and history.LastModifiedDate is None
    ]


def _unassigned(model: Any) -> list[Any]:
    return [
        item
        for item in model.by_type("IfcStyledItem")
        if any(s.is_a("IfcPresentationStyle") for s in item.Styles or ())
    ]


def _needs(model: Any, schema: str) -> dict[str, dict[str, Any]]:
    """What ``schema``, the one the migration ends in, needs and ``model`` lacks, with the options for each. The
    migrator makes up values for some of it, as an owner history for each instance, so it is put right first."""
    found = {}
    source, target = _family(model.schema_identifier), _family(schema)
    if target == "IFC2X3" and source != "IFC2X3":
        lacking = [e for e in model.by_type("IfcRoot") if e.OwnerHistory is None]
        if lacking:
            found[OWNER] = _problem(OWNER, OWNER, lacking, ["owner"], "IFC2X3 needs an owner history on each of these")
        if items := _unassigned(model):
            message = "IFC2X3 needs the styles of a styled item in a style assignment"
            found[STYLES] = _problem(STYLES, STYLES, items, ["assignment", "leave_out"], message)
    if source == "IFC2X3" and target != "IFC2X3":
        bare = [e for e in model.by_type("IfcRelSpaceBoundary") if e.RelatedBuildingElement is None]
        if bare:
            message = "IFC4 needs a building element on every space boundary, and these have none"
            found[BOUNDARY] = _problem(BOUNDARY, BOUNDARY, bare, ["virtual", "leave_out"], message)
        if undated := _undated(model):
            message = "IFC4 needs a date of change on an owner history that says what changed, and these have none"
            found[DATES] = _problem(DATES, DATES, undated, ["dated"], message)
    return found


def _declared(schema: str, name: str) -> bool:
    import ifcopenshell.ifcopenshell_wrapper

    try:
        ifcopenshell.ifcopenshell_wrapper.schema_by_name(schema).declaration_by_name(name)
    except Exception:  # the wrapper says so with a RuntimeError
        return False
    return True


def _ahead(model: Any, steps: list[str], problems: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """What the last step of a migration through IFC4 will not carry, found by class before the first step is made:
    a class that the first step keeps, or maps, and the last schema has no equivalent of."""
    import ifcopenshell.util.schema

    if len(steps) < 2:
        return {}
    migrator = ifcopenshell.util.schema.Migrator()
    tables = {"IFC2X3": migrator.class_2x3_to_4, "IFC4": migrator.class_4_to_2x3}
    by_class: dict[str, list[Any]] = {}
    for element in model:
        name = element.is_a()
        if name in problems:
            continue
        after = name if _declared(steps[0], name) else tables.get(_family(model.schema_identifier), {}).get(name)
        if after and not _declared(steps[-1], after):
            later = tables.get(_family(steps[0]), {}).get(after) if _family(steps[0]) == "IFC4" else None
            if not (later and _declared(steps[-1], later)):
                by_class.setdefault(after, []).append(element)
    return {
        name: {
            **_problem(name, "no_equivalent", elements, _options(name, elements), f"{steps[-1]} has no {name}"),
            "later": True,
        }
        for name, elements in sorted(by_class.items())
    }


def _problem(key: str, kind: str, elements: list[Any], options: list[str], message: str) -> dict[str, Any]:
    examples, examples_cut = _capped(
        [{"step": e.id(), "global_id": e.GlobalId if e.is_a("IfcRoot") else None} for e in elements]
    )
    affected: set[str] = set()
    for element in elements:
        affected |= _products_of(element)
        if len(affected) > MAX_LISTED:
            break
    affects, affects_cut = _capped(sorted(affected))
    return {
        "key": key,
        "kind": kind,
        "ifc_class": elements[0].is_a(),
        "count": len(elements),
        "examples": examples,
        "examples_cut": examples_cut,
        "affects": affects,
        "affects_cut": affects_cut or len(affected) > MAX_LISTED,
        "message": message,
        "options": options,
    }


def _to_polyline(model: Any, curve: Any) -> None:
    """An IfcIndexedPolyCurve of straight segments as an IfcPolyline of the same points, in its place."""
    import ifcopenshell.util.element

    rows = curve.Points.CoordList
    if curve.Segments is None:
        order = list(range(1, len(rows) + 1))
    else:
        order = []
        for segment in curve.Segments:
            for index in segment.wrappedValue:
                if not order or order[-1] != index:
                    order.append(index)
    points = [model.createIfcCartesianPoint([float(v) for v in rows[i - 1]]) for i in order]
    line = model.createIfcPolyline(points)
    for referrer in model.get_inverse(curve):
        ifcopenshell.util.element.replace_attribute(referrer, curve, line)
    held = curve.Points
    model.remove(curve)
    if not model.get_inverse(held):
        model.remove(held)


def _owner(model: Any) -> Any:
    """An owner history made by IFC-LPG, for what IFC2X3 needs one on and has none: added now, which both IFC2X3 and
    IFC4 take with its date of change."""
    organisation = model.createIfcOrganization(None, "IFC-LPG", None, None, None)
    person = model.createIfcPerson(None, "IFC-LPG", None, None, None, None, None, None)
    user = model.createIfcPersonAndOrganization(person, organisation, None)
    application = model.createIfcApplication(organisation, "1.0.0", "IFC-LPG", "IFC-LPG")
    now = int(time.time())
    return model.createIfcOwnerHistory(user, application, None, "ADDED", now, None, None, now)


def _chosen(problems: dict[str, dict[str, Any]], choices: dict[str, str], key: str) -> str | None:
    """The choice made for a problem, which has to be one of its options."""
    if key not in problems:
        return None
    choice, options = choices[key], problems[key]["options"]
    if choice not in options:
        raise ValueError(f"{choice!r} is no option for {key}; those are {', '.join(options) or 'none'}")
    return choice


def _resolve_needs(model: Any, key: str, choice: str, moved: dict[int, int | None]) -> None:
    """Do the choice for what the schema needs and the model lacks, on ``model``."""
    import ifcopenshell.guid

    if key == OWNER:
        history = _owner(model)
        for element in model.by_type("IfcRoot"):
            if element.OwnerHistory is None:
                element.OwnerHistory = history
    elif key == DATES:
        for history in _undated(model):
            history.LastModifiedDate = history.CreationDate
    elif key == BOUNDARY:
        for boundary in [e for e in model.by_type("IfcRelSpaceBoundary") if e.RelatedBuildingElement is None]:
            if choice == "virtual":
                boundary.RelatedBuildingElement = model.create_entity(
                    "IfcVirtualElement",
                    GlobalId=ifcopenshell.guid.new(),
                    OwnerHistory=boundary.OwnerHistory,
                    Name=boundary.Name or "Virtual boundary",
                )
            else:
                moved[boundary.id()] = None
                model.remove(boundary)
    elif key == STYLES:
        for item in _unassigned(model):
            if choice == "assignment":
                direct = [style for style in item.Styles if style.is_a("IfcPresentationStyle")]
                kept = [style for style in item.Styles if not style.is_a("IfcPresentationStyle")]
                item.Styles = [*kept, model.createIfcPresentationStyleAssignment(direct)]
            else:
                moved[item.id()] = None
                model.remove(item)


def _resolve(model: Any, problems: dict[str, dict[str, Any]], choices: dict[str, str], moved: dict[int, int | None]):
    """Do the choices for the classes the schema has no equivalent of, on ``model``. ``moved`` says what became of an
    instance's number."""
    import ifcopenshell.api.root

    for key in problems:
        choice = _chosen(problems, choices, key)
        for element in list(model.by_type(key, include_subtypes=False)):
            old = element.id()
            if choice == "type":
                operation = getattr(element, "OperationType", None)
                kind = ifcopenshell.api.root.reassign_class(model, product=element, ifc_class=_STYLES[key])
                _fill_type(kind, operation)
                moved[old] = kind.id()
            elif choice == "proxy":
                is_type = element.is_a("IfcTypeObject")
                proxy = "IfcBuildingElementProxyType" if is_type else "IfcBuildingElementProxy"
                made = ifcopenshell.api.root.reassign_class(model, product=element, ifc_class=proxy)
                said = "ElementType" if is_type else "ObjectType"
                if getattr(made, said, "") is None:
                    setattr(made, said, key)  # what it was, where nothing else is said
                moved[old] = made.id()
            elif choice == "leave_out":
                if element.is_a("IfcRelationship"):
                    model.remove(element)
                else:
                    ifcopenshell.api.root.remove_product(model, product=element)
                moved[old] = None
            elif choice == "polyline":
                _to_polyline(model, element)
                moved[old] = None


def _fill_type(kind: Any, operation: Any) -> None:
    """What a type that was a style must have: how a window is partitioned, or a door opens, from the style's
    operation where the type has that value, and otherwise NOTDEFINED; and NOTDEFINED as its predefined type."""
    import ifcopenshell.ifcopenshell_wrapper
    import ifcopenshell.util.attribute

    schema = ifcopenshell.ifcopenshell_wrapper.schema_by_name(kind.file.schema)
    declaration: Any = schema.declaration_by_name(kind.is_a()).as_entity()
    attributes = {attribute.name(): attribute for attribute in declaration.all_attributes()}
    name = "PartitioningType" if kind.is_a("IfcWindowType") else "OperationType"
    allowed = ifcopenshell.util.attribute.get_enum_items(attributes[name])
    setattr(kind, name, operation if operation in allowed else "NOTDEFINED")
    if getattr(kind, "PredefinedType", "x") is None:
        kind.PredefinedType = "NOTDEFINED"


def _steps(source: str, target: str) -> list[str]:
    """The schemas a migration goes through: IFC4 between IFC2X3 and IFC4X3, for the migrator maps those to IFC4."""
    if {_family(source), _family(target)} == {"IFC2X3", "IFC4X3"}:
        return ["IFC4", target]
    return [target]


def _migrator(model: Any, schema: str) -> tuple[Any, Any]:
    """A migrator to ``schema`` and its file, with ``model`` made ready for it: the migrator takes out first what the
    schema has no more, such as IFC4's IfcPresentationStyleAssignment in IFC4X3."""
    import ifcopenshell
    import ifcopenshell.util.schema

    migrated = ifcopenshell.file(schema=_wanted(schema))
    migrator = ifcopenshell.util.schema.Migrator()
    with contextlib.redirect_stdout(sys.stderr):
        migrator.preprocess(model, migrated)
    return migrator, migrated


def _migrate(model: Any, migrator: Any, migrated: Any, moved: dict[int, int | None]) -> Any:
    with contextlib.redirect_stdout(sys.stderr):  # the migrator prints what it cannot carry over
        for element in model:
            migrator.migrate(element, migrated)
    mapping = migrator.migrated_ids
    for old, current in list(moved.items()):
        moved[old] = mapping.get(current) if current is not None else None
    for element in model:
        if element.id() not in moved:
            moved[element.id()] = mapping.get(element.id())
    return migrated


def _check(source: Path, target: Path, moved: dict[int, int | None]) -> list[dict[str, Any]]:
    """The issues of the result that the file did not have, the file's carried over by ``moved``."""
    from ifc_lpg.validation import issues_of

    def key(issue: dict[str, Any], instance: Any) -> tuple[Any, ...]:
        if instance is None:
            return (None, issue["attribute"], str(issue["message"]).split("\n", 1)[0])
        return (instance, issue["attribute"])

    before = {key(issue, moved.get(issue["instance"], issue["instance"])) for issue in issues_of(source)}
    new = [issue for issue in issues_of(target) if key(issue, issue["instance"]) not in before]
    return [
        {
            "kind": issue["kind"] or "schema",
            "severity": "error",
            "message": str(issue["message"])[:1000],
            "instance": issue["instance"],
            "attribute": issue["attribute"],
        }
        for issue in new
    ]


def prepare_file(
    source: Path,
    target: Path,
    *,
    schema: str | None,
    length_unit: str | None,
    choices: dict[str, str] | None = None,
    check: bool = True,
) -> dict[str, Any]:
    """Write ``source`` migrated to ``schema`` and with its lengths in ``length_unit`` to ``target``. Either can be
    left out. Returns what was done; or, with nothing written, the ``problems`` that need ``choices``, or the ``new``
    issues the check found."""
    import ifcopenshell
    import ifcopenshell.util.unit

    choices = choices or {}
    instances = len(StepFile(source.read_bytes()))  # the file is whole, or it is refused here
    model = ifcopenshell.open(str(source))
    if sum(1 for _ in model) != instances:
        raise ValueError(f"IfcOpenShell read {sum(1 for _ in model)} of the file's {instances} instances")
    answer: dict[str, Any] = {"schema": [model.schema_identifier, model.schema_identifier], "resolutions": choices}
    moved: dict[int, int | None] = {}
    if schema and schema.upper() != model.schema_identifier.upper():
        steps = _steps(model.schema_identifier, schema)
        answer["schema"][1] = schema
        needs = _needs(model, schema)
        for index, step in enumerate(steps):
            migrator, migrated = _migrator(model, step)
            problems = _problems(model, step)
            if index == 0:
                listed = {**needs, **problems, **_ahead(model, steps, problems)}
                unresolved = [problem for key, problem in listed.items() if key not in choices]
                for key in listed.keys() & choices.keys():
                    _chosen(listed, choices, key)  # a choice that is no option stops it before anything is done
            else:
                unresolved = [problem for key, problem in problems.items() if key not in choices]
            if unresolved:
                return {**answer, "problems": unresolved}
            for key in needs:
                # Each where the model can take it: the styles' assignment only in IFC4, just before IFC2X3.
                at_last = key == STYLES
                if (at_last and index == len(steps) - 1) or (not at_last and index == 0):
                    _resolve_needs(model, key, str(_chosen(needs, choices, key)), moved)
            _resolve(model, problems, choices, moved)
            model = _migrate(model, migrator, migrated, moved)
        answer["schema"][1] = model.schema_identifier
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
    if check:
        new = _check(source, target, moved)
        if new:
            target.unlink()
            listed, cut = _capped(new)
            return {**answer, "check": "failed", "new_count": len(new), "new": listed, "new_cut": cut}
        answer["check"] = "passed"
    return answer


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ifc-lpg-prepare")
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument("--schema")
    parser.add_argument("--length-unit")
    parser.add_argument("--resolve", action="append", default=[], help="KEY=OPTION, a class or owner_history")
    parser.add_argument("--no-check", action="store_true")
    parser.add_argument("--memory-mb", type=int, default=0)
    args = parser.parse_args(argv)
    if args.memory_mb:
        limit_memory(args.memory_mb)
    try:
        choices = dict(item.split("=", 1) for item in args.resolve)
        if any(choice not in RESOLUTIONS for choice in choices.values()):
            raise ValueError(f"a choice is one of {', '.join(RESOLUTIONS)}")
        answer = prepare_file(
            args.source,
            args.target,
            schema=args.schema,
            length_unit=args.length_unit,
            choices=choices,
            check=not args.no_check,
        )
    except (StepError, ValueError, RuntimeError, OSError) as exc:
        sys.stderr.write(json.dumps({"error": f"{type(exc).__name__}: {str(exc)[:500]}"}) + "\n")
        return 3
    sys.stdout.write(json.dumps(answer) + "\n")
    sys.stdout.flush()
    return 1 if "problems" in answer else 2 if answer.get("check") == "failed" else 0


if __name__ == "__main__":
    keep_stdout_for_the_answer()
    sys.exit(main())
