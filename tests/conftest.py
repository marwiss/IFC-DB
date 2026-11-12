# This file was generated with the assistance of an AI coding tool.
"""Shared test data, and the graph databases the round trip is tried against.

Neo4j always; Memgraph too when ``IFC_DB_TEST_MEMGRAPH=1``. One container per test session, emptied before each test.
Grafeo has fixtures of its own, a server for each module of tests when ``IFC_DB_TEST_GRAFEO=1``, and skips otherwise.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import ssl
import time
import urllib.request
from collections.abc import AsyncIterator, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

NEO4J_IMAGE = "neo4j:5.26.32-community"
MEMGRAPH_IMAGE = "memgraph/memgraph:3.14.0"
NEO4J_PASSWORD = "integration-test-password"
GRAFEO_IMAGE = (  # pinned by its digest, for Grafeo is before 1.0
    "grafeo/grafeo-server:0.6.2-full@sha256:e1bcdac60c1c9517cf9e2388eb492495b0efcb6228aad8fb88c56bb9d8f28a1f"
)


@dataclass(frozen=True)
class Target:
    dialect: str
    uri: str
    auth: tuple[str, str] | None


def _targets() -> list[str]:
    return ["neo4j", "memgraph"] if os.environ.get("IFC_DB_TEST_MEMGRAPH") == "1" else ["neo4j"]


@pytest.fixture(scope="session", params=_targets())
def target(request: pytest.FixtureRequest) -> Iterator[Target]:
    from testcontainers.core.container import DockerContainer
    from testcontainers.core.wait_strategies import LogMessageWaitStrategy

    if request.param == "neo4j":
        container = (
            DockerContainer(NEO4J_IMAGE)
            .with_env("NEO4J_AUTH", f"neo4j/{NEO4J_PASSWORD}")
            .with_env("NEO4J_server_memory_heap_max__size", "512m")
            .with_env("NEO4J_server_memory_pagecache_size", "128m")
            .with_exposed_ports(7687)
            .waiting_for(LogMessageWaitStrategy(re.compile(r"Started\.")).with_startup_timeout(120))
        )
        auth: tuple[str, str] | None = ("neo4j", NEO4J_PASSWORD)
    else:
        container = (
            DockerContainer(MEMGRAPH_IMAGE)
            .with_exposed_ports(7687)
            .waiting_for(LogMessageWaitStrategy(re.compile(r"You are running Memgraph")).with_startup_timeout(120))
        )
        auth = ("", "")  # Memgraph takes empty credentials; no auth at all is refused
    with container:
        uri = f"bolt://{container.get_container_host_ip()}:{container.get_exposed_port(7687)}"
        yield Target(request.param, uri, auth)


@pytest.fixture
async def graph(target: Target) -> AsyncIterator[object]:
    from neo4j import AsyncGraphDatabase
    from neo4j.exceptions import ServiceUnavailable

    from ifc_db.graph import Neo4jGraph, ensure_schema

    driver = AsyncGraphDatabase.driver(target.uri, auth=target.auth)
    try:
        for wait in range(60, -1, -1):  # Memgraph logs that it runs a moment before it answers on Bolt
            try:
                await driver.verify_connectivity()
                break
            except ServiceUnavailable:
                if not wait:
                    raise
                await asyncio.sleep(0.5)
        graph = Neo4jGraph(driver)
        await graph.run("MATCH (n) DETACH DELETE n")
        await ensure_schema(graph, "neo4j" if target.dialect == "neo4j" else "memgraph")
        yield graph
    finally:
        await driver.close()


def needs_grafeo() -> None:
    if os.environ.get("IFC_DB_TEST_GRAFEO") != "1":
        pytest.skip("Grafeo is tried with IFC_DB_TEST_GRAFEO=1")


@contextlib.contextmanager
def grafeo_server(name: str, env: Mapping[str, str], certificates: Path | None = None) -> Iterator[str]:
    """A Grafeo server on two cores and four gigabytes, named as this project's, with its certificates in ``/certs``.
    Yields its address once it answers: ``https://`` with certificates, ``http://`` without."""
    from testcontainers.core.container import DockerContainer
    from testcontainers.core.wait_strategies import LogMessageWaitStrategy

    container = (
        DockerContainer(GRAFEO_IMAGE, nano_cpus=2_000_000_000, mem_limit="4g")
        .with_name(f"ifclpg-grafeo-{name}-{os.getpid()}")
        .with_exposed_ports(7474)
        .waiting_for(LogMessageWaitStrategy(re.compile(r"Grafeo Server ready")).with_startup_timeout(120))
    )
    for key, value in env.items():
        container = container.with_env(key, value)
    if certificates is not None:
        container = container.with_volume_mapping(str(certificates), "/certs", "ro")
    with container:
        scheme = "http" if certificates is None else "https"
        url = f"{scheme}://{container.get_container_host_ip()}:{container.get_exposed_port(7474)}"
        context = None if certificates is None else ssl.create_default_context(cafile=certificates / "ca.pem")
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context)
        )
        for wait in range(60, -1, -1):  # /health answers without credentials
            try:
                with opener.open(f"{url}/health", timeout=5):
                    break
            except OSError:
                if not wait:
                    raise
                time.sleep(0.5)
        yield url


@pytest.fixture(scope="module")
def grafeo_url() -> Iterator[str]:
    """A Grafeo server over HTTP for a module of tests, so that only one runs at a time."""
    needs_grafeo()
    with grafeo_server("http", {"GRAFEO_ALLOW_UNAUTHENTICATED": "true"}) as url:  # it listens on this machine alone
        yield url


@pytest.fixture
async def grafeo(grafeo_url: str) -> AsyncIterator[object]:
    """Grafeo's backend on an empty database, with its schema."""
    from ifc_db.backends.grafeo import GrafeoBackend

    async with GrafeoBackend(grafeo_url) as backend:
        await backend.run("MATCH (n) DETACH DELETE n")
        await backend.ensure_schema()
        yield backend
