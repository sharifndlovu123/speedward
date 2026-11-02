"""docker-compose.yml: each container gets only its own settings. The MaxMind licence key must never
reach the speedward container, and the Globalping key never the geoipupdate one.

The resolved config is read with `docker compose config` against a dummy env file (never the real
.env) and an empty process environment; skipped when Docker Compose is not installed.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DUMMY = ("GLOBALPING_TOKEN=dummy-globalping\nGEOIPUPDATE_ACCOUNT_ID=111111\n"
         "GEOIPUPDATE_LICENSE_KEY=dummy-licence\nGEOIP_DB=/host/only/path.mmdb\nSECRET_KEY=dummy-secret\n")


def _config(tmp_path, *files) -> dict:
    if shutil.which("docker") is None:
        pytest.skip("docker is not installed")
    env_file = tmp_path / "dummy.env"
    env_file.write_text(DUMMY)
    args = ["docker", "compose", "--project-directory", str(ROOT), "--env-file", str(env_file)]
    for f in files:
        args += ["-f", str(ROOT / f)]
    proc = subprocess.run(args + ["config", "--format", "json"], capture_output=True, text=True,
                          env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(tmp_path)})
    if proc.returncode != 0 and "is not a docker command" in proc.stderr:
        pytest.skip("docker compose is not installed")
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_compose_file_never_hands_over_dotenv_wholesale():
    text = (ROOT / "docker-compose.yml").read_text()
    assert "env_file" not in text


@pytest.mark.parametrize("files", [("docker-compose.yml",), ("docker-compose.yml", "docker-compose.secrets.yml")])
def test_each_service_gets_only_its_own_keys(tmp_path, files):
    services = _config(tmp_path, *files)["services"]
    app = services["speedward"]["environment"]
    geo = services["geoipupdate"]["environment"]
    assert not [k for k in app if k.startswith("GEOIPUPDATE_")]
    assert "dummy-licence" not in json.dumps(services["speedward"])
    assert app["GEOIP_DB"] == "/geo/GeoLite2-City.mmdb"  # never the host path from .env
    assert app["GLOBALPING_TOKEN"] == "dummy-globalping"
    assert "GLOBALPING_TOKEN" not in geo and "dummy-globalping" not in json.dumps(services["geoipupdate"])
    mounts = services["speedward"]["volumes"]
    assert mounts[0]["target"] == "/geo" and mounts[0]["read_only"] is True
    assert services["speedward"]["ports"][0]["host_ip"] == "127.0.0.1"


def test_secrets_override_passes_files_not_values(tmp_path):
    geo = _config(tmp_path, "docker-compose.yml", "docker-compose.secrets.yml")["services"]["geoipupdate"]
    env = geo["environment"]
    assert env["GEOIPUPDATE_LICENSE_KEY_FILE"] == "/run/secrets/maxmind_license_key"
    assert env["GEOIPUPDATE_ACCOUNT_ID_FILE"] == "/run/secrets/maxmind_account_id"
    assert "GEOIPUPDATE_LICENSE_KEY" not in env and "GEOIPUPDATE_ACCOUNT_ID" not in env


def test_image_build_context_excludes_secrets_and_location_files():
    ignored = (ROOT / ".dockerignore").read_text().split()
    for pattern in (".env", "secrets/", "*.mmdb", "GeoIP.conf"):
        assert pattern in ignored
