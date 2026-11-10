# This file was generated with the assistance of an AI coding tool.
"""A whole IFC file into the graph, and back out as a file.

:func:`import_file` converts a file in a process of its own, with a time limit and a memory limit (POSIX), loads the
records into the graph and marks the model ``done``. :func:`export_file` writes a model back from what the graph holds,
in a process of its own too. Programs with their own idea of a model, such as Dataregium, which ties a model to a
project and a document version, use the parts directly: :func:`convert_file`, :func:`~ifc_lpg.importer.import_records`,
:func:`~ifc_lpg.exporter.export_records` and :func:`write_file`. :func:`check_ids` checks a file against IDS files, in a
process of its own as well.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ifc_lpg.exporter import export_records
from ifc_lpg.graph import Graph
from ifc_lpg.importer import delete_model, import_records


@dataclass(frozen=True)
class Limits:
    """What a conversion or a writing may take. A crash or a hang ends its process, not the caller."""

    timeout_seconds: float = 900.0
    memory_mb: int = 4096
    max_entities: int = 5_000_000


DEFAULT_LIMITS = Limits()


class ConversionFailed(RuntimeError):
    """The file could not be converted, written or checked. The message says why, and never quotes the file at
    length."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


async def run_worker(command: list[str], what: str, seconds: float, *, answers: tuple[int, ...] = (0,)) -> str:
    """Run a worker with a time limit. Returns what it wrote to stdout. An exit code in ``answers`` is an answer, and any
    other a failure."""
    process = await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=seconds)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise ConversionFailed(f"{what} took longer than {seconds:.0f} s") from None
    if process.returncode not in answers:
        lines = stderr.decode("utf-8", "replace").strip().splitlines()
        detail = lines[-1] if lines else f"exit code {process.returncode}"
        with contextlib.suppress(ValueError, AttributeError):
            detail = json.loads(detail).get("error", detail)
        raise ConversionFailed(f"{what} failed: {detail}")
    return stdout.decode("utf-8")


async def convert_file(
    source: Path,
    records: Path,
    *,
    lossless: bool = True,
    include_geometry: bool = False,
    geometry_in_blocks: bool = False,
    limits: Limits = DEFAULT_LIMITS,
) -> dict[str, Any]:
    """Convert ``source`` into records at ``records``. Returns the header: schema, the file's header and the counts.

    The geometry is a skeleton by default: what shapes are made of stays STEP text in blocks, and the rest are nodes.
    ``include_geometry`` makes every instance a node, and ``geometry_in_blocks`` keeps all geometry in blocks."""
    command = [
        sys.executable,
        "-m",
        "ifc_lpg.worker",
        str(source),
        str(records),
        "--memory-mb",
        str(limits.memory_mb),
        "--max-entities",
        str(limits.max_entities),
    ]
    if include_geometry:
        command.append("--include-geometry")
    if geometry_in_blocks:
        command.append("--geometry-in-blocks")
    if lossless:
        command.append("--lossless")
    answer = await run_worker(command, "the conversion", limits.timeout_seconds)
    return json.loads(answer.strip().splitlines()[-1])


async def write_file(records: Path, target: Path, *, limits: Limits = DEFAULT_LIMITS) -> None:
    """Write the records of an exported model as an IFC file at ``target``."""
    command = [sys.executable, "-m", "ifc_lpg.writer", str(records), str(target), "--memory-mb", str(limits.memory_mb)]
    await run_worker(command, "writing the file", limits.timeout_seconds)


async def check_ids(source: Path, ids_files: list[Path], *, limits: Limits = DEFAULT_LIMITS) -> dict[str, Any]:
    """Check ``source`` against the IDS files ``ids_files``. Returns the report of :func:`ifc_lpg.ids.report_of`: the
    issues, each with the index of its IDS, the IDS files that could not be read, and a summary of every specification."""
    paths = [str(source), *(str(path) for path in ids_files)]
    command = [sys.executable, "-m", "ifc_lpg.ids", "--memory-mb", str(limits.memory_mb), "--", *paths]
    # The check exits with 1 when something failed, which is an answer like any other.
    answer = await run_worker(command, "the IDS check", limits.timeout_seconds, answers=(0, 1))
    return json.loads(answer.strip().splitlines()[-1])


async def import_file(
    graph: Graph,
    source: Path,
    model_id: str,
    *,
    lossless: bool = True,
    include_geometry: bool = False,
    geometry_in_blocks: bool = False,
    limits: Limits = DEFAULT_LIMITS,
) -> dict[str, Any]:
    """Convert ``source`` and load it into the graph as the model ``model_id``, which must not exist yet.

    The ``IfcModel`` node is ``running`` while it loads and ``done`` with its counts afterwards. If anything fails, what
    was loaded is taken out again and the model is ``failed``, with the error. Returns the model's properties."""
    rows = await graph.run(
        "MERGE (m:IfcModel {id: $id}) ON CREATE SET m.state = 'running', m.running_at = $now, m.file_name = $name "
        "RETURN m.state AS state, m.running_at = $now AS new",
        id=model_id,
        now=_now(),
        name=source.name,
    )
    if not rows or not rows[0]["new"]:
        raise ValueError(f"the model {model_id!r} exists already")
    try:
        with tempfile.TemporaryDirectory(prefix="ifc-lpg-") as tmp:
            records = Path(tmp) / "records.jsonl"
            header = await convert_file(
                source,
                records,
                lossless=lossless,
                include_geometry=include_geometry,
                geometry_in_blocks=geometry_in_blocks,
                limits=limits,
            )
            counts = await import_records(graph, model_id, records)
    except Exception as exc:
        with contextlib.suppress(Exception):
            await delete_model(graph, model_id, keep_model=True)
        error = f"{type(exc).__name__}: {exc}"[:500]
        await graph.run(
            "MATCH (m:IfcModel {id: $id}) SET m.state = 'failed', m.failed_at = $now, m.error = $error",
            id=model_id,
            now=_now(),
            error=error,
        )
        raise
    fields = {
        "schema": header.get("schema"),
        "ifc_project_guid": header.get("ifc_project_guid"),
        "entities": counts["nodes"],
        "relationships": counts["relationships"],
        "skipped": header.get("skipped", 0),
        "lossless": header.get("lossless", False),
        "geometry": header.get("geometry"),
        "ifc_header": header.get("ifc_header"),
        "done_at": _now(),
    }
    await graph.run("MATCH (m:IfcModel {id: $id}) SET m.state = 'done', m += $fields", id=model_id, fields=fields)
    return {"id": model_id, "state": "done", **fields}


async def export_file(graph: Graph, model_id: str, target: Path, *, limits: Limits = DEFAULT_LIMITS) -> int:
    """Write the model ``model_id`` back as an IFC file at ``target``. Returns the number of instances written."""
    with tempfile.TemporaryDirectory(prefix="ifc-lpg-") as tmp:
        records = Path(tmp) / "records.jsonl"
        instances = await export_records(graph, model_id, records)
        await write_file(records, target, limits=limits)
    return instances
