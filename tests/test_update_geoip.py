"""scripts/update_geoip.sh: argument handling, the mode-600 rule, the verify step and the DB-IP month
fallback. No network: geoipupdate and curl are replaced by small fake scripts, and the database is a
tiny hand-built MaxMind DB file (one search-tree node that sends every IPv4 address to one record).
"""
import gzip
import os
import shutil
import stat
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("maxminddb")  # the verify step needs it (pip install -e ".[geoip]")

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "update_geoip.sh"
pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


# --- a minimal MMDB writer (enough types for a City-shaped record) --------------------------------

def _ctrl(type_: int, size: int) -> bytes:
    assert size < 29
    if type_ <= 7:
        return bytes([(type_ << 5) | size])
    return bytes([size, type_ - 7])  # extended type


def _enc(value) -> bytes:
    if isinstance(value, str):
        raw = value.encode()
        return _ctrl(2, len(raw)) + raw
    if isinstance(value, float):
        return _ctrl(3, 8) + struct.pack(">d", value)
    if isinstance(value, dict):
        return _ctrl(7, len(value)) + b"".join(_enc(k) + _enc(v) for k, v in value.items())
    if isinstance(value, list):
        return _ctrl(11, len(value)) + b"".join(_enc(v) for v in value)
    if isinstance(value, tuple):  # (type, int): unsigned integers of a given MMDB type
        type_, n = value
        raw = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
        return _ctrl(type_, len(raw)) + raw
    raise TypeError(value)


def tiny_mmdb(record: dict) -> bytes:
    node_count = 1
    pointer = node_count + 16  # "data section, offset 0"
    tree = pointer.to_bytes(3, "big") * 2  # record size 24: left and right both point at the record
    meta = {"node_count": (6, node_count), "record_size": (5, 24), "ip_version": (5, 4),
            "database_type": "GeoLite2-City", "languages": ["en"],
            "binary_format_major_version": (5, 2), "binary_format_minor_version": (5, 0),
            "build_epoch": (9, int(time.time())), "description": {"en": "speedward test"}}
    return tree + b"\x00" * 16 + _enc(record) + b"\xab\xcd\xefMaxMind.com" + _enc(meta)


CITY = tiny_mmdb({"location": {"latitude": 37.4, "longitude": -122.1}})
NO_LOCATION = tiny_mmdb({"country": {"iso_code": "US"}})


# --- helpers ---------------------------------------------------------------------------------------

def _fake(path: Path, body: str) -> Path:
    path.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + body)
    path.chmod(0o755)
    return path


def run(tmp_path, *args, **env):
    full = {"PATH": os.environ["PATH"], "HOME": str(tmp_path / "home"), "GEOIP_DIR": str(tmp_path / "geo"),
            "GEOIP_PYTHON": sys.executable, **env}
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env=full, timeout=60)


@pytest.fixture
def conf(tmp_path):
    p = tmp_path / "GeoIP.conf"
    p.write_text("AccountID YOUR_ACCOUNT_ID\nLicenseKey placeholder-not-a-real-key\nEditionIDs GeoLite2-City\n")
    p.chmod(0o600)
    return p


@pytest.fixture
def fake_geoipupdate(tmp_path):
    db = tmp_path / "source.mmdb"
    db.write_bytes(CITY)
    # Copies the database into the -d folder, as geoipupdate does.
    return _fake(tmp_path / "geoipupdate", f'dir=""\nwhile [ $# -gt 0 ]; do [ "$1" = -d ] && dir="$2"; shift; done\n'
                                           f'cp "{db}" "$dir/GeoLite2-City.mmdb"\n'), db


def fake_curl(tmp_path, codes: dict, payload: bytes):
    """A curl that answers each month URL with the given HTTP code (200 writes `payload`, gzipped)."""
    gz = tmp_path / "payload.gz"
    gz.write_bytes(gzip.compress(payload))
    cases = "\n".join(f'  *{month}*) code={code} ;;' for month, code in codes.items())
    return _fake(tmp_path / "curl", f'''out=""; url=""
while [ $# -gt 0 ]; do
  case "$1" in --output) out="$2"; shift 2 ;; --write-out) shift 2 ;; -*) shift ;; *) url="$1"; shift ;; esac
done
echo "$url" >> "{tmp_path}/urls.log"
code=404
case "$url" in
{cases}
esac
[ "$code" = 200 ] && cp "{gz}" "$out"
printf '%s' "$code"
''')


def months():
    now = subprocess.run(["date", "+%Y-%m"], capture_output=True, text=True).stdout.strip()
    prev = subprocess.run(["date", "-d", f"{now}-01 -1 month", "+%Y-%m"], capture_output=True, text=True).stdout.strip()
    return now, prev


# --- arguments -------------------------------------------------------------------------------------

def test_help_and_bad_arguments(tmp_path):
    assert run(tmp_path, "--help").returncode == 0
    bad = run(tmp_path, "--source", "geoip-r-us")
    assert bad.returncode != 0 and "maxmind or dbip" in bad.stderr
    assert run(tmp_path, "--frobnicate").returncode != 0
    assert run(tmp_path, "--source").returncode != 0


def test_script_is_executable_in_the_repo():
    assert SCRIPT.stat().st_mode & stat.S_IXUSR


# --- maxmind -----------------------------------------------------------------------------------------

def test_maxmind_refuses_a_readable_config(tmp_path, conf, fake_geoipupdate):
    conf.chmod(0o644)
    r = run(tmp_path, GEOIP_CONF=str(conf), GEOIPUPDATE=str(fake_geoipupdate[0]))
    assert r.returncode != 0 and "chmod 600" in r.stderr
    assert not (tmp_path / "geo" / "GeoLite2-City.mmdb").exists()


def test_maxmind_updates_verifies_and_never_prints_the_config(tmp_path, conf, fake_geoipupdate):
    r = run(tmp_path, GEOIP_CONF=str(conf), GEOIPUPDATE=str(fake_geoipupdate[0]))
    assert r.returncode == 0, r.stderr
    assert "updated: " in r.stdout and "GeoLite2-City.mmdb" in r.stdout and "bytes" in r.stdout
    assert "placeholder-not-a-real-key" not in r.stdout + r.stderr


def test_maxmind_fails_when_the_file_has_no_location(tmp_path, conf, fake_geoipupdate):
    fake_geoipupdate[1].write_bytes(NO_LOCATION)
    r = run(tmp_path, GEOIP_CONF=str(conf), GEOIPUPDATE=str(fake_geoipupdate[0]))
    assert r.returncode != 0 and "not a usable City database" in r.stderr


def test_maxmind_without_binary_or_config(tmp_path, conf):
    r = run(tmp_path, GEOIP_CONF=str(tmp_path / "missing.conf"))
    assert r.returncode != 0 and "no MaxMind config" in r.stderr
    r = run(tmp_path, GEOIP_CONF=str(conf), PATH="/usr/bin:/bin")
    assert r.returncode != 0 and "geoipupdate not found" in r.stderr


# --- dbip ----------------------------------------------------------------------------------------------

def test_dbip_falls_back_to_last_month_and_swaps_atomically(tmp_path):
    now, prev = months()
    curl = fake_curl(tmp_path, {now: 404, prev: 200}, CITY)
    r = run(tmp_path, "--source", "dbip", GEOIP_CURL=str(curl))
    assert r.returncode == 0, r.stderr
    target = tmp_path / "geo" / "dbip-city-lite-current.mmdb"
    assert target.read_bytes() == CITY
    assert f"from DB-IP {prev}" in r.stdout
    urls = (tmp_path / "urls.log").read_text().split()
    assert urls == [f"https://download.db-ip.com/free/dbip-city-lite-{now}.mmdb.gz",
                    f"https://download.db-ip.com/free/dbip-city-lite-{prev}.mmdb.gz"]
    assert [p.name for p in (tmp_path / "geo").iterdir()] == ["dbip-city-lite-current.mmdb"]  # no temp left


def test_dbip_keeps_the_old_file_when_the_new_one_fails_to_verify(tmp_path):
    now, _ = months()
    geo = tmp_path / "geo"
    geo.mkdir()
    (geo / "dbip-city-lite-current.mmdb").write_bytes(CITY)
    curl = fake_curl(tmp_path, {now: 200}, NO_LOCATION)
    r = run(tmp_path, "--source", "dbip", GEOIP_CURL=str(curl))
    assert r.returncode != 0
    assert (geo / "dbip-city-lite-current.mmdb").read_bytes() == CITY
    assert [p.name for p in geo.iterdir()] == ["dbip-city-lite-current.mmdb"]


def test_dbip_stops_on_other_http_errors(tmp_path):
    now, _ = months()
    r = run(tmp_path, "--source", "dbip", GEOIP_CURL=str(fake_curl(tmp_path, {now: 503}, CITY)))
    assert r.returncode != 0 and "HTTP 503" in r.stderr


# --- check -----------------------------------------------------------------------------------------------

def test_check_reports_age_and_fails_when_old_or_missing(tmp_path):
    assert run(tmp_path, "--check").returncode == 1
    geo = tmp_path / "geo"
    geo.mkdir()
    db = geo / "GeoLite2-City.mmdb"
    db.write_bytes(CITY)
    fresh = run(tmp_path, "--check")
    assert fresh.returncode == 0 and "0 days old" in fresh.stdout
    old = time.time() - 50 * 86400
    os.utime(db, (old, old))
    stale = run(tmp_path, "--check")
    assert stale.returncode == 1 and "older than 45 days" in stale.stdout
    assert run(tmp_path, "--check", "--source", "dbip").returncode == 1  # its own file, not there
