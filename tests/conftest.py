# This file was generated with the assistance of an AI coding tool.
"""Shared test data, and the graph databases the round trip is tried against.

Neo4j always; Memgraph too when ``IFC_LPG_TEST_MEMGRAPH=1``. One container per test session, emptied before each test.
"""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

NEO4J_IMAGE = "neo4j:5.26.32-community"
MEMGRAPH_IMAGE = "memgraph/memgraph:3.14.0"
NEO4J_PASSWORD = "integration-test-password"


@dataclass(frozen=True)
class Target:
    dialect: str
    uri: str
    auth: tuple[str, str] | None


def _targets() -> list[str]:
    return ["neo4j", "memgraph"] if os.environ.get("IFC_LPG_TEST_MEMGRAPH") == "1" else ["neo4j"]


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

    from ifc_lpg.graph import Neo4jGraph, ensure_schema

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
