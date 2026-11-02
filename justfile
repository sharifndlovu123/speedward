# Task runner: https://github.com/casey/just  (installed into the venv by `pip install -e ".[dev]"`)
# Run `just` to list every task (system install, or `.venv/bin/just` from the venv).

venv := ".venv/bin"

# List the tasks
default:
    @{{just_executable()}} --list --unsorted

# Lint (never run `ruff format`: the code is not auto-formatted)
lint:
    {{venv}}/ruff check .

# Unit tests (pass extra pytest args, e.g. `just test tests/test_runner.py -v`)
test *args:
    {{venv}}/pytest -q {{args}}

# Lint and unit tests, the same as CI's fast jobs
check: lint test

# Browser tests (installs the e2e extra and Chromium on first use)
e2e:
    {{venv}}/pip install -e ".[e2e]"
    {{venv}}/playwright install chromium
    {{venv}}/pytest e2e

# Web app locally with debug on (open http://127.0.0.1:8000)
serve:
    DEBUG=1 {{venv}}/django-admin runserver --settings=speedward.web.settings

# Estimate the test cost of a scan without spending any tests
estimate url:
    {{venv}}/speedward scan {{url}} --estimate

# Run a real scan (uses real tests; only for sites you own)
scan url *args:
    {{venv}}/speedward scan {{url}} {{args}}

# Start the Docker stack: speedward plus the location-file updater
up:
    docker compose up -d --build

# Stop the Docker stack
down:
    docker compose down

# Follow the speedward container logs
logs:
    docker compose logs -f speedward

# Check how old the location file is (add `--source dbip` to download the no-account one)
geoip *args="--check":
    scripts/update_geoip.sh {{args}}
