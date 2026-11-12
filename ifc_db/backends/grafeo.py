# This file was generated with the assistance of an AI coding tool.
"""Grafeo: a model in GQL (ISO/IEC 39075), over Grafeo's HTTP API with Python's standard library.

Each statement is a ``POST /query`` on one connection that stays open between statements. Grafeo's Bolt server waits
about 45 ms on every statement, since it does not set TCP_NODELAY; Bolt, with Neo4j's driver, comes back when that is
mended. HTTPS is the rule: an ``https://`` address always has its certificate checked, against the system's
authorities or a given CA file, and credentials (Grafeo's token, or a user and a password) go only over HTTPS, or over
HTTP to this machine.

The graph has Neo4j's shape: ``Entity`` nodes with the class and its supertypes as labels, references as relationships
with their ``position``, blocks as ``StepBlock`` nodes, and the record as a ``Model``. Grafeo has no points, so a row of
points is a list of lists of numbers, which is what an export gives for Neo4j's points too. Every value is a parameter,
in the tagged form of Grafeo's values (``{"Float64": 1.0}``), so that a number keeps its type: a literal with a decimal
point is an exact DECIMAL in GQL. An answer's DECIMAL is a :class:`decimal.Decimal`, and any other value JSON has no
form for a :class:`Native`. Labels and relationship types are checked as identifiers and quoted. The schema is
constraints and indexes in the default graph, with no graph type: a graph type that names a node type takes the type
out of the default graph (Grafeo's #730 and #764).
"""

from __future__ import annotations

import asyncio
import base64
import decimal
import http.client
import ipaddress
import json
import re
import ssl
import time
import urllib.parse
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from ifc_db.backends.base import Backend
from ifc_db.graph import INDEXED, UNIQUE
from ifc_db.records import BATCH, Block, NewEntity, Reference
from ifc_db.schema import safe_identifier

MAX_BODY = 2 * 1024 * 1024  # the largest request Grafeo takes unless it is started with a larger --max-body-size
IDLE_SECONDS = 15.0  # a connection that has stood idle longer is opened anew, before the server closes it
BLOCK_BATCH = 100  # blocks are deleted a few at a time, for each holds a lot of text
_CODE = re.compile(r"GRAFEO-[A-Z]+[0-9]+")


class GrafeoError(RuntimeError):
    """Grafeo refused a statement, or gave no answer. ``status`` is the HTTP status, None without an answer, and
    ``code`` Grafeo's own code, such as ``GRAFEO-Q001``, when it gave one."""

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


class ConstraintViolation(GrafeoError):
    """A constraint of the schema refused the change: a value that must be unique or given, or of a type."""


@dataclass(frozen=True)
class Native:
    """A value of Grafeo's that JSON has no form for, as an answer gives it (``{"$kind": value}``)."""

    kind: str
    value: Any


def tagged(value: Any) -> Any:
    """A Python value as Grafeo's HTTP API reads a parameter."""
    if value is None:
        return "Null"
    if isinstance(value, bool):
        return {"Bool": value}
    if isinstance(value, int):
        return {"Int64": value}
    if isinstance(value, float):
        return {"Float64": value}
    if isinstance(value, str):
        return {"String": value}
    if isinstance(value, list | tuple):
        return {"List": [tagged(item) for item in value]}
    if isinstance(value, Mapping):
        return {"Map": {key: tagged(item) for key, item in value.items()}}
    raise TypeError(f"Grafeo has no parameter for a {type(value).__name__}")


def plain(value: Any) -> Any:
    """A value of an answer as Python has it."""
    if isinstance(value, list):
        return [plain(item) for item in value]
    if isinstance(value, dict):
        if len(value) == 1:
            ((key, item),) = value.items()
            if key == "$decimal":
                return decimal.Decimal(item)
            if key.startswith("$"):
                return Native(key[1:], item)
        return {key: plain(item) for key, item in value.items()}
    return value


def rows_of(status: int, answer: bytes) -> list[dict[str, Any]]:
    """The rows of an answer as dicts, or the refusal as an error."""
    if status != 200:
        text = answer.decode("utf-8", "replace")
        try:
            refusal = json.loads(text)
            text = str(refusal.get("detail") or refusal.get("error"))
        except (ValueError, AttributeError):
            pass
        if status == 413:
            text += "; Grafeo takes larger statements when it is started with a larger --max-body-size"
        code = _CODE.search(text)
        kind = ConstraintViolation if "constraint violation" in text.lower() else GrafeoError
        raise kind(f"Grafeo answered {status}: {text[:500]}", status=status, code=code.group() if code else None)
    result = json.loads(answer)
    columns = result["columns"]
    return [dict(zip(columns, map(plain, row), strict=True)) for row in result["rows"]]


def _loopback(host: str) -> bool:
    try:
        return host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")


class _Http:
    """One connection to Grafeo's HTTP API, opened anew when it has stood idle or broken."""

    def __init__(
        self,
        url: str,
        *,
        token: str | None,
        user: str | None,
        password: str | None,
        ca_file: str | Path | None,
        timeout: float,
        max_body: int,
    ) -> None:
        parts = urllib.parse.urlsplit(url)
        if parts.username is not None or parts.password is not None:
            raise ValueError("credentials go as a token or a user and a password, not in the address")
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError(f"not an http:// or https:// address: {parts.scheme}://{parts.netloc}")
        if token is not None and user is not None:
            raise ValueError("a token, or a user and a password, not both")
        if (user is None) != (password is None):
            raise ValueError("a user needs a password, and a password a user")
        secure = parts.scheme == "https"
        if not secure and (token is not None or user is not None) and not _loopback(parts.hostname):
            raise ValueError("credentials go only over HTTPS, or over HTTP to this machine")
        if not secure and ca_file is not None:
            raise ValueError("a CA file is for an https:// address")
        self.address = f"{parts.scheme}://{parts.netloc}"
        self._host, self._port, self._path = parts.hostname, parts.port, parts.path.rstrip("/") + "/query"
        self._context = ssl.create_default_context(cafile=ca_file) if secure else None
        self._headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if token is not None:
            self._headers["Authorization"] = f"Bearer {token}"
        elif user is not None:
            pair = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
            self._headers["Authorization"] = f"Basic {pair}"
        self._timeout, self.max_body = timeout, max_body
        self._connection: http.client.HTTPConnection | None = None
        self._used = 0.0
        self._lock = asyncio.Lock()

    async def query(
        self, statement: str, params: Mapping[str, Any], rows: Sequence[Any] | None = None
    ) -> list[dict[str, Any]]:
        """The rows of a statement. ``rows``, the parameter ``$rows``, may be sent in parts, each a statement of its
        own, so that no request is larger than ``max_body``; a single row that is larger goes alone."""
        values = {key: tagged(value) for key, value in params.items()}
        if rows is None:
            return await self._send(self._body(statement, values))
        head, _, tail = self._body(statement, {**values, "rows": {"List": []}}).rpartition(b"[]")
        found: list[dict[str, Any]] = []
        part: list[bytes] = []
        size = len(head) + len(tail) + 2
        for row in rows:
            piece = _json(tagged(row))
            if part and size + len(piece) + 1 > self.max_body:
                found += await self._send(head + b"[" + b",".join(part) + b"]" + tail)
                part, size = [], len(head) + len(tail) + 2
            part.append(piece)
            size += len(piece) + 1
        if part:
            found += await self._send(head + b"[" + b",".join(part) + b"]" + tail)
        return found

    def _body(self, statement: str, values: Mapping[str, Any]) -> bytes:
        timeout_ms = int(self._timeout * 1000)
        return _json({"query": statement, "language": "gql", "timeout_ms": timeout_ms, "params": values})

    async def _send(self, body: bytes) -> list[dict[str, Any]]:
        async with self._lock:
            status, answer = await asyncio.to_thread(self._post, body)
        return rows_of(status, answer)

    def _post(self, body: bytes) -> tuple[int, bytes]:
        if self._connection is not None and time.monotonic() - self._used > IDLE_SECONDS:
            self.close()
        if self._connection is None:
            if self._context is None:
                self._connection = http.client.HTTPConnection(self._host, self._port, timeout=self._timeout)
            else:
                self._connection = http.client.HTTPSConnection(
                    self._host, self._port, timeout=self._timeout, context=self._context
                )
        try:
            self._connection.request("POST", self._path, body=body, headers=self._headers)
            response = self._connection.getresponse()
            answer = response.read()
        except ssl.SSLCertVerificationError as exc:
            self.close()
            raise GrafeoError(f"the certificate of {self.address} is not trusted: {exc.verify_message}") from exc
        except (OSError, http.client.HTTPException) as exc:
            self.close()
            message = f"no answer from {self.address}, so the statement may not have been carried out: {exc}"
            raise GrafeoError(message) from exc
        self._used = time.monotonic()
        if response.will_close:
            self.close()
        return response.status, answer

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None


def _name(name: str) -> str:
    return f"`{safe_identifier(name)}`"


def schema_statements() -> list[str]:
    """The constraints and indexes the statements look nodes up by. A unique constraint makes an index of its own."""
    statements = [
        f"CREATE CONSTRAINT IF NOT EXISTS {_name(f'{label}_{prop}_unique')} FOR (n:{_name(label)}) "
        f"ON (n.{_name(prop)}) UNIQUE"
        for label, prop in UNIQUE
    ]
    statements += [
        f"CREATE INDEX IF NOT EXISTS {_name(f'{label}_{prop}_index')} FOR (n:{_name(label)}) ON (n.{_name(prop)})"
        for label, prop in INDEXED
    ]
    return statements


class GrafeoBackend(Backend):
    """A Grafeo server at ``url``, ``https://…`` or ``http://…``, with Grafeo's ``token`` or a ``user`` and
    ``password`` if it asks for them. ``ca_file`` holds the authorities that an HTTPS server's certificate is checked
    against, in place of the system's. ``max_body`` is the largest request the server takes."""

    name: ClassVar[str] = "grafeo"

    def __init__(
        self,
        url: str,
        *,
        token: str | None = None,
        user: str | None = None,
        password: str | None = None,
        ca_file: str | Path | None = None,
        timeout: float = 300.0,
        max_body: int = MAX_BODY,
    ) -> None:
        self._http = _Http(
            url, token=token, user=user, password=password, ca_file=ca_file, timeout=timeout, max_body=max_body
        )

    async def close(self) -> None:
        self._http.close()

    async def __aenter__(self) -> GrafeoBackend:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    async def run(self, statement: str, /, **params: Any) -> list[dict[str, Any]]:
        """A GQL statement with its values as parameters, committed on its own. Returns the rows as dicts."""
        return await self._http.query(statement, params)

    async def _each(self, statement: str, rows: Sequence[Any], **params: Any) -> None:
        """A statement over ``$rows``, sent in as many parts as the server's limit asks for."""
        await self._http.query(statement, params, rows)

    async def ensure_schema(self) -> None:
        """Make the constraints and indexes that are not there yet."""
        for statement in schema_statements():
            await self.run(statement)

    async def counts(self, model_id: str) -> dict[str, int]:
        """What the database holds of a model: its nodes, the relationships between them, its blocks and the instances
        in those. Grafeo has taken statements it did not carry out without a word, so a load is counted after."""
        entities = await self.run("MATCH (n:Entity {model_id: $id}) RETURN count(n) AS n", id=model_id)
        relationships = await self.run(
            "MATCH (:Entity {model_id: $id})-[r]->(:Entity) RETURN count(r) AS n", id=model_id
        )
        sizes = await self.block_sizes(model_id)
        return {
            "entities": entities[0]["n"],
            "relationships": relationships[0]["n"],
            "blocks": len(sizes),
            "in_blocks": sum(sizes.values()),
        }

    # The model's record

    async def model(self, model_id: str) -> dict[str, Any] | None:
        rows = await self.run("MATCH (m:Model {id: $id}) RETURN properties(m) AS properties", id=model_id)
        return rows[0]["properties"] if rows else None

    async def models(self) -> list[dict[str, Any]]:
        return await self.run(
            "MATCH (m:Model) RETURN m.id AS id, m.imported_at AS imported_at, m.state AS state ORDER BY id"
        )

    async def start_import(self, model_id: str, form_version: int) -> None:
        await self.run(
            "MERGE (m:Model {id: $id}) SET m += $facts REMOVE m.imported_at",
            id=model_id,
            facts={"format": "step", "form_version": form_version},
        )

    async def set_facts(self, model_id: str, facts: Mapping[str, Any]) -> None:
        await self._update(model_id, facts, ())

    async def _update(self, model_id: str, facts: Mapping[str, Any], remove: Iterable[str]) -> None:
        """Set ``facts`` on the record, and take ``remove`` away and every fact that is None."""
        gone = sorted({*remove, *(key for key, value in facts.items() if value is None)})
        kept = {key: value for key, value in facts.items() if value is not None}
        removal = f" REMOVE {', '.join(f'm.{_name(key)}' for key in gone)}" if gone else ""
        await self.run(f"MATCH (m:Model {{id: $id}}) SET m += $facts{removal}", id=model_id, facts=kept)

    # A load

    async def add_entities(self, model_id: str, entities: Sequence[NewEntity]) -> None:
        """One statement for each set of labels. A row's values are its properties, its rows of points and its keys."""
        groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
        for entity in entities:
            keys = {"id": f"{model_id}:{entity.step}", "step_id": entity.step, "model_id": model_id}
            groups.setdefault(entity.labels, []).append({**entity.props, **entity.points, **keys})
        for labels, rows in groups.items():
            label_set = "&".join(_name(label) for label in ("Entity", *labels))
            await self._each(f"FOR row IN $rows INSERT (n:{label_set}) SET n += row", rows)

    async def add_blocks(self, model_id: str, blocks: Sequence[Block]) -> None:
        """A text too long for one request goes in pieces, each added to what the block holds."""
        piece = self._http.max_body // 2
        await self._each(
            """
            MATCH (m:Model {id: $model_id})
            FOR row IN $rows
            INSERT (m)-[:HAS_STEP_BLOCK]->(:StepBlock {
                id: row.id, first_step: row.first, instances: row.instances, text: row.text
            })
            """,
            [
                {"id": f"{model_id}:{b.first}", "first": b.first, "instances": b.instances, "text": b.text[:piece]}
                for b in blocks
            ],
            model_id=model_id,
        )
        for block in blocks:
            for start in range(piece, len(block.text), piece):
                await self.run(
                    "MATCH (b:StepBlock {id: $id}) SET b.text = b.text || $piece",
                    id=f"{model_id}:{block.first}",
                    piece=block.text[start : start + piece],
                )

    async def add_references(self, model_id: str, references: Sequence[Reference]) -> None:
        """One statement for each type of relationship, with its position or, for an inverse one, without."""
        groups: dict[tuple[str, bool], list[dict[str, Any]]] = {}
        for reference in references:
            row = {"a": f"{model_id}:{reference.a}", "b": f"{model_id}:{reference.b}", "position": reference.position}
            groups.setdefault((reference.type, reference.position is not None), []).append(row)
        for (kind, positioned), rows in groups.items():
            position = " {position: row.position}" if positioned else ""
            await self._each(
                "FOR row IN $rows MATCH (a:Entity {id: row.a}), (b:Entity {id: row.b}) "
                f"INSERT (a)-[:{_name(kind)}{position}]->(b)",
                rows,
            )

    async def link_project(self, model_id: str) -> None:
        await self.run(
            """
            MATCH (m:Model {id: $id}), (p:Entity&IfcProject {model_id: $id})
            WHERE NOT EXISTS { MATCH (m)-[:HAS_PROJECT]->(p) }
            INSERT (m)-[:HAS_PROJECT]->(p)
            """,
            id=model_id,
        )

    # What an export reads back

    async def steps(self, model_id: str) -> list[int]:
        rows = await self.run("MATCH (n:Entity {model_id: $id}) RETURN n.step_id AS step", id=model_id)
        return [row["step"] for row in rows]

    async def entity_page(self, model_id: str, steps: Sequence[int]) -> list[tuple[int, dict[str, Any], list[Any]]]:
        """The nodes and their references in two statements, put together here."""
        ids = [f"{model_id}:{step}" for step in steps]
        nodes = await self.run(
            "FOR node_id IN $ids MATCH (n:Entity {id: node_id}) RETURN n.step_id AS step, properties(n) AS props",
            ids=ids,
        )
        found = await self.run(
            """
            FOR node_id IN $ids
            MATCH (n:Entity {id: node_id})-[r]->(t:Entity)
            WHERE r.position IS NOT NULL
            RETURN n.step_id AS step, type(r) AS kind, r.position AS position, t.step_id AS target
            """,
            ids=ids,
        )
        references: dict[int, list[Any]] = {}
        for row in found:
            references.setdefault(row["step"], []).append([row["kind"], row["position"], row["target"]])
        return [(row["step"], row["props"], references.get(row["step"], [])) for row in nodes]

    async def block_sizes(self, model_id: str) -> dict[int, int]:
        rows = await self.run(
            "MATCH (:Model {id: $id})-[:HAS_STEP_BLOCK]->(b:StepBlock) RETURN b.first_step AS first, "
            "b.instances AS instances",
            id=model_id,
        )
        return {row["first"]: row["instances"] for row in rows}

    async def block_text(self, model_id: str, first: int) -> str:
        found = await self.run("MATCH (b:StepBlock {id: $id}) RETURN b.text AS text", id=f"{model_id}:{first}")
        return found[0]["text"]

    # Taking a model out

    async def delete_blocks(self, model_id: str) -> None:
        firsts = sorted(await self.block_sizes(model_id))
        for start in range(0, len(firsts), BLOCK_BATCH):
            await self.run(
                "FOR block_id IN $ids MATCH (b:StepBlock {id: block_id}) DETACH DELETE b",
                ids=[f"{model_id}:{first}" for first in firsts[start : start + BLOCK_BATCH]],
            )

    async def delete_entities(self, model_id: str) -> int:
        """In batches of nodes found by their ``id``, so that the work ends whatever a statement does."""
        rows = await self.run("MATCH (n:Entity {model_id: $id}) RETURN n.id AS id", id=model_id)
        ids = [row["id"] for row in rows]
        for start in range(0, len(ids), BATCH):
            await self.run(
                "FOR node_id IN $ids MATCH (n:Entity {id: node_id}) DETACH DELETE n", ids=ids[start : start + BATCH]
            )
        return len(ids)

    async def delete_record(self, model_id: str, *, keep: bool) -> None:
        if keep:
            await self._update(model_id, {}, ("imported_at",))
        else:
            await self.run(
                "MATCH (m:Model {id: $id}) WHERE coalesce(m.format, $step) = $step DETACH DELETE m",
                id=model_id,
                step="step",
            )
