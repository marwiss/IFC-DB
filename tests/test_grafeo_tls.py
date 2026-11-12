# This file was generated with the assistance of an AI coding tool.
"""Grafeo over HTTPS with a token: a file comes back identical, and a wrong token or authority is refused.

The certificates are made for the test with ``openssl``, an authority and a server certificate for this machine, and
another authority that did not sign it. They live in the test's own folder and nowhere else.
"""

from __future__ import annotations

import secrets
import shutil
import ssl
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from ifc_db.backends.grafeo import GrafeoBackend, GrafeoError
from ifc_db.equivalence import compare_files
from ifc_db.exporter import export_records
from ifc_db.files import convert_file, write_file
from ifc_db.importer import import_records
from tests.conftest import FIXTURES_DIR, grafeo_server, needs_grafeo

pytestmark = pytest.mark.integration
pytest.importorskip("ifcopenshell", reason="IfcOpenShell is not installed")

TOKEN = secrets.token_urlsafe(24)
WALLS = FIXTURES_DIR / "three_walls_gtin.ifc"
SERVER = """[server]
subjectAltName = DNS:localhost, IP:127.0.0.1
basicConstraints = critical, CA:FALSE
keyUsage = critical, digitalSignature
extendedKeyUsage = serverAuth
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid, issuer
"""


EC = "-newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes"
MAKE = (  # an authority, another, and a certificate for this machine signed by the first
    f"req -x509 {EC} -days 2 -keyout ca.key -out ca.pem -subj /CN=IFC-DB-ca -addext basicConstraints=critical,CA:TRUE "
    "-addext keyUsage=critical,keyCertSign,cRLSign",
    f"req -x509 {EC} -days 2 -keyout other.key -out other.pem -subj /CN=IFC-DB-other "
    "-addext basicConstraints=critical,CA:TRUE -addext keyUsage=critical,keyCertSign,cRLSign",
    f"req {EC} -subj /CN=localhost -keyout server.key -out server.csr",
    "x509 -req -in server.csr -CA ca.pem -CAkey ca.key -CAcreateserial -days 2 -extfile server.cnf -extensions server "
    "-out server.pem",
)


@pytest.fixture(scope="module")
def certificates(tmp_path_factory: pytest.TempPathFactory) -> Path:
    needs_grafeo()
    openssl = shutil.which("openssl")
    if openssl is None:
        pytest.skip("openssl makes the test's certificates")
    folder = tmp_path_factory.mktemp("grafeo-tls")
    (folder / "server.cnf").write_text(SERVER, encoding="ascii")
    for arguments in MAKE:
        subprocess.run([openssl, *arguments.split()], cwd=folder, check=True, capture_output=True)  # noqa: S603
    (folder / "server.key").chmod(0o644)  # a key of the test's own, for the server's user in the container
    return folder


@pytest.fixture(scope="module")
def grafeo_https(certificates: Path) -> Iterator[str]:
    env = {"GRAFEO_TLS_CERT": "/certs/server.pem", "GRAFEO_TLS_KEY": "/certs/server.key", "GRAFEO_AUTH_TOKEN": TOKEN}
    with grafeo_server("https", env, certificates) as url:
        yield url


async def test_a_file_comes_back_identical_over_https_with_a_token(
    grafeo_https: str, certificates: Path, tmp_path: Path
) -> None:
    records, out, target = tmp_path / "records.jsonl", tmp_path / "out.jsonl", tmp_path / "back.ifc"
    await convert_file(WALLS, records)
    async with GrafeoBackend(grafeo_https, token=TOKEN, ca_file=certificates / "ca.pem") as grafeo:
        await grafeo.ensure_schema()
        await import_records(grafeo, "walls", records)
        await export_records(grafeo, "walls", out)
    await write_file(out, target)
    assert compare_files(WALLS, target).identical


@pytest.mark.parametrize("token", ["not-the-token", None])
async def test_https_refuses_a_wrong_token_or_none(grafeo_https: str, certificates: Path, token: str | None) -> None:
    async with GrafeoBackend(grafeo_https, token=token, ca_file=certificates / "ca.pem") as grafeo:
        with pytest.raises(GrafeoError) as refused:
            await grafeo.run("RETURN 1 AS n")
    assert refused.value.status == 401 and TOKEN not in str(refused.value)


@pytest.mark.parametrize("authority", ["other.pem", None])
async def test_a_certificate_no_trusted_authority_signed_is_refused(
    grafeo_https: str, certificates: Path, authority: str | None
) -> None:
    ca_file = None if authority is None else certificates / authority
    async with GrafeoBackend(grafeo_https, token=TOKEN, ca_file=ca_file) as grafeo:
        with pytest.raises(GrafeoError, match="not trusted") as refused:
            await grafeo.run("RETURN 1 AS n")
    assert isinstance(refused.value.__cause__, ssl.SSLCertVerificationError) and TOKEN not in str(refused.value)
