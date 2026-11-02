#!/usr/bin/env bash
# Download or refresh the offline City database speedward uses to place answering servers.
#
#   scripts/update_geoip.sh                    MaxMind GeoLite2 City through geoipupdate (default)
#   scripts/update_geoip.sh --source dbip      DB-IP Lite City, no account needed (monthly file)
#   scripts/update_geoip.sh --check [--source dbip]
#                                              report the current file's age; exit 1 if missing or
#                                              older than 45 days
#
# Settings (environment):
#   GEOIP_DIR    where the .mmdb lives (default $HOME/geo)
#   GEOIP_CONF   MaxMind config with your account ID and licence key (default $HOME/geo/GeoIP.conf);
#                must be mode 600. Its contents are never printed.
#   GEOIP_PYTHON a Python with maxminddb, used to verify the file (default: the repo's .venv, else python3)
#   GEOIPUPDATE  path to the geoipupdate binary (default: PATH, else $HOME/geo/bin/geoipupdate_*/geoipupdate)
#   GEOIP_CURL   curl to use for DB-IP (default curl)
#
# Prints only which file was updated, its size and date. Exits non-zero on any failure.
set -euo pipefail

MAX_AGE_DAYS=45
TEST_IP=8.8.8.8
DBIP_NAME=dbip-city-lite-current.mmdb
MAXMIND_NAME=GeoLite2-City.mmdb

die() { echo "update_geoip: $*" >&2; exit 1; }

usage() {
    sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

source_name=maxmind
check_only=0
while [ $# -gt 0 ]; do
    case "$1" in
        --source)
            [ $# -ge 2 ] || die "--source needs maxmind or dbip"
            source_name="$2"; shift 2 ;;
        --source=*) source_name="${1#--source=}"; shift ;;
        --check) check_only=1; shift ;;
        -h|--help) usage 0 ;;
        *) echo "update_geoip: unknown argument: $1" >&2; usage 2 ;;
    esac
done
case "$source_name" in
    maxmind) target_name="$MAXMIND_NAME" ;;
    dbip) target_name="$DBIP_NAME" ;;
    *) die "--source must be maxmind or dbip" ;;
esac

geo_dir="${GEOIP_DIR:-$HOME/geo}"
target="$geo_dir/$target_name"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -n "${GEOIP_PYTHON:-}" ]; then
    python="$GEOIP_PYTHON"
elif [ -x "$script_dir/../.venv/bin/python" ]; then
    python="$script_dir/../.venv/bin/python"
else
    python=python3
fi

describe() {  # path -> "path (N bytes, modified YYYY-MM-DD)"
    echo "$1 ($(stat -c %s "$1") bytes, modified $(date -r "$1" +%Y-%m-%d))"
}

verify() {  # path: opens with maxminddb and places a known public address
    "$python" -I - "$1" "$TEST_IP" <<'PY' || die "the downloaded file is not a usable City database"
import sys
try:
    import maxminddb
except ImportError:
    sys.exit("maxminddb is not installed for this Python (pip install -e '.[geoip]', or set GEOIP_PYTHON)")
with maxminddb.open_database(sys.argv[1]) as reader:
    record = reader.get(sys.argv[2])
location = (record or {}).get("location") or {}
if not isinstance(location.get("latitude"), (int, float)) or not isinstance(location.get("longitude"), (int, float)):
    sys.exit("no location for the test address")
PY
}

if [ "$check_only" = 1 ]; then
    [ -f "$target" ] || { echo "missing: $target"; exit 1; }
    age_days=$(( ( $(date +%s) - $(stat -c %Y "$target") ) / 86400 ))
    echo "$(describe "$target"), $age_days days old"
    [ "$age_days" -le "$MAX_AGE_DAYS" ] || { echo "older than $MAX_AGE_DAYS days: update it"; exit 1; }
    exit 0
fi

mkdir -p "$geo_dir"

if [ "$source_name" = maxmind ]; then
    conf="${GEOIP_CONF:-$HOME/geo/GeoIP.conf}"
    [ -f "$conf" ] || die "no MaxMind config at $conf (see docs/GeoIP.conf.example)"
    mode=$(stat -c %a "$conf")
    [ $(( 8#$mode & 8#077 )) -eq 0 ] || die "$conf is readable by other users (mode $mode); run: chmod 600 $conf"
    bin="${GEOIPUPDATE:-}"
    if [ -z "$bin" ]; then
        bin="$(command -v geoipupdate || true)"
    fi
    if [ -z "$bin" ]; then
        for candidate in "$HOME"/geo/bin/geoipupdate_*/geoipupdate; do
            [ -x "$candidate" ] && bin="$candidate"
        done
    fi
    [ -n "$bin" ] && [ -x "$bin" ] || die "geoipupdate not found (install it, see README)"
    # geoipupdate writes atomically and keeps the old file when a download fails.
    "$bin" -f "$conf" -d "$geo_dir" >/dev/null || die "geoipupdate failed"
    [ -f "$target" ] || die "geoipupdate did not produce $target (is EditionIDs GeoLite2-City?)"
    verify "$target"
    echo "updated: $(describe "$target")"
    exit 0
fi

# DB-IP Lite City: a new file each month; early in a month it may not be published yet.
curl_bin="${GEOIP_CURL:-curl}"
tmp="$(mktemp "$geo_dir/.dbip-download.XXXXXX")"
trap 'rm -f "$tmp" "$tmp.gz"' EXIT
downloaded=0
for months_back in 0 1; do
    month="$(date -d "$(date +%Y-%m-01) -$months_back month" +%Y-%m)"
    url="https://download.db-ip.com/free/dbip-city-lite-$month.mmdb.gz"
    code="$("$curl_bin" --silent --show-error --location --output "$tmp.gz" --write-out '%{http_code}' "$url")" \
        || die "download failed for $month"
    if [ "$code" = 200 ]; then
        downloaded=1
        break
    fi
    [ "$code" = 404 ] || die "download for $month answered HTTP $code"
done
[ "$downloaded" = 1 ] || die "no DB-IP file for this month or the last"
gunzip -c "$tmp.gz" > "$tmp" || die "the download is not a valid gzip file"
verify "$tmp"
chmod 644 "$tmp"
mv -f "$tmp" "$target"  # same folder, so the swap is atomic; the old file stays until here
echo "updated: $(describe "$target") from DB-IP $month"
