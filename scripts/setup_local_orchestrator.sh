#!/bin/bash
# One-time setup for the local orchestrator: python env + local tracking DB.
# Uses pyenv-virtualenv when available (env name: pipelines), else falls back to .venv.
set -e

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
ORCH="$REPO_ROOT/local_orchestrator"
ENV_NAME="pipelines"

if command -v pyenv > /dev/null && pyenv virtualenv --version > /dev/null 2>&1; then
    if ! pyenv versions --bare | grep -qx ".*envs/$ENV_NAME\|$ENV_NAME"; then
        # Prefer the newest installed 3.10+ (google libs dropped 3.8/3.9)
        PY_BASE=$(pyenv versions --bare | grep -E '^3\.1[0-9]+\.' | sort -V | tail -1)
        echo "--- Creating pyenv virtualenv '$ENV_NAME' (base: ${PY_BASE:-global}) ---"
        pyenv virtualenv ${PY_BASE:+"$PY_BASE"} "$ENV_NAME"
    fi
    (cd "$REPO_ROOT" && pyenv local "$ENV_NAME")
    PYBIN=$(PYENV_VERSION="$ENV_NAME" pyenv which python)
    RUN_PREFIX="python"
    echo "--- Using pyenv virtualenv '$ENV_NAME' ($PYBIN) ---"
else
    echo "--- pyenv not found, creating venv at $REPO_ROOT/.venv ---"
    python3 -m venv "$REPO_ROOT/.venv"
    PYBIN="$REPO_ROOT/.venv/bin/python"
    RUN_PREFIX="$REPO_ROOT/.venv/bin/python"
fi

"$PYBIN" -m pip install --upgrade pip -q
"$PYBIN" -m pip install -r "$ORCH/requirements.txt"

echo "--- Verifying pinned subdron-utils ---"
"$PYBIN" -c "
import subdron_utils
from importlib.metadata import version
assert hasattr(subdron_utils, 'get_mission_info'), 'get_mission_info missing — wrong version?'
print('subdron_utils', version('subdron_utils'), 'OK')
"

echo "--- Starting local mission DB (postgres on :${MISSION_DB_PORT:-5433}) ---"
docker compose -f "$ORCH/docker-compose.yml" up -d
until docker exec subdron-mission-db pg_isready -U subdron -d mission_data -q 2>/dev/null; do
    sleep 1
done

echo "--- Smoke-testing DB logging ---"
DB_POSTGRES_HOST=localhost DB_POSTGRES_PORT=${MISSION_DB_PORT:-5433} \
DB_POSTGRES_USER=subdron DB_POSTGRES_PASSWORD=subdron_local \
DB_POSTGRES_DB=mission_data PIPELINE_ENV=dev \
"$PYBIN" -c "
import subdron_utils
subdron_utils.log_mission_stage(mission_id='SETUP_SMOKE_TEST', timestamp='2000-01-01-00-00-00', stage='SETUP', status='Done')
"
docker exec subdron-mission-db psql -U subdron -d mission_data -tAc \
    "DELETE FROM missions_test WHERE mission_id = 'SETUP_SMOKE_TEST';" > /dev/null

echo ""
echo "Setup complete. Run a bag with:"
echo "  $RUN_PREFIX local_orchestrator/run_extraction.py <bag_path>"
