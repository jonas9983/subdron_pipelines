#!/bin/bash
# One-time setup for the local orchestrator: python env + local tracking DB.
# Uses pyenv-virtualenv when available (env name: pipelines), else falls back to .venv.
# Safe to run as a second user on a machine that is already set up: the running
# DB is adopted (not recreated) and its port auto-detected.
#   --data-root PATH   also write DATA_ROOT/MISSION_DB_PORT exports to ~/.bashrc
set -e

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
ORCH="$REPO_ROOT/local_orchestrator"
ENV_NAME="pipelines"

DATA_ROOT_ARG=""
if [ "$1" = "--data-root" ]; then
    DATA_ROOT_ARG="$2"
    [ -n "$DATA_ROOT_ARG" ] || { echo "--data-root needs a path" >&2; exit 1; }
fi

# Shared data root: first run on a machine creates the group + group-writable
# tree (needs sudo once); later runs just join the current user to the group.
if [ -n "$DATA_ROOT_ARG" ]; then
    if [ ! -d "$DATA_ROOT_ARG" ] || ! getent group subdron > /dev/null; then
        echo "--- One-time shared data root setup: $DATA_ROOT_ARG (sudo) ---"
        sudo groupadd -f subdron
        sudo mkdir -p "$DATA_ROOT_ARG"
        sudo chown -R "$USER:subdron" "$DATA_ROOT_ARG"
        sudo chmod -R 2775 "$DATA_ROOT_ARG"              # setgid: new files inherit the group
        sudo setfacl -R -d -m g::rwx "$DATA_ROOT_ARG"    # new files stay group-writable
    fi
    if ! getent group subdron | grep -qw "$USER"; then
        echo "--- Adding $USER to group 'subdron' (sudo; takes effect after re-login) ---"
        sudo usermod -aG subdron "$USER"
    fi
fi

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

if ! docker info > /dev/null 2>&1; then
    echo "!!! No docker access — add the group and re-run:  sudo usermod -aG docker $USER  (then log out/in)" >&2
    exit 1
fi

if docker inspect -f '{{.State.Running}}' subdron-mission-db 2>/dev/null | grep -qx true; then
    DB_PORT=$(docker port subdron-mission-db 5432/tcp | head -1 | awk -F: '{print $NF}')
    echo "--- Mission DB already running on :$DB_PORT — reusing it ---"
else
    DB_PORT=${MISSION_DB_PORT:-5433}
    echo "--- Starting local mission DB (postgres on :$DB_PORT) ---"
    docker compose -f "$ORCH/docker-compose.yml" up -d
fi
until docker exec subdron-mission-db pg_isready -U subdron -d mission_data -q 2>/dev/null; do
    sleep 1
done

echo "--- Smoke-testing DB logging ---"
DB_POSTGRES_HOST=localhost DB_POSTGRES_PORT=$DB_PORT \
DB_POSTGRES_USER=subdron DB_POSTGRES_PASSWORD=subdron_local \
DB_POSTGRES_DB=mission_data PIPELINE_ENV=dev \
"$PYBIN" -c "
import subdron_utils
subdron_utils.log_mission_stage(mission_id='SETUP_SMOKE_TEST', timestamp='2000-01-01-00-00-00', stage='SETUP', status='Done')
"
docker exec subdron-mission-db psql -U subdron -d mission_data -tAc \
    "DELETE FROM missions_test WHERE mission_id = 'SETUP_SMOKE_TEST';" > /dev/null

if [ -n "$DATA_ROOT_ARG" ]; then
    echo "--- Writing exports to ~/.bashrc ---"
    grep -q "^export DATA_ROOT=" ~/.bashrc 2>/dev/null \
        || echo "export DATA_ROOT=$DATA_ROOT_ARG" >> ~/.bashrc
    grep -q "^export MISSION_DB_PORT=" ~/.bashrc 2>/dev/null \
        || echo "export MISSION_DB_PORT=$DB_PORT" >> ~/.bashrc
fi

MISSING_GROUPS=""
for g in subdron systemd-journal; do
    getent group "$g" 2>/dev/null | grep -qw "$USER" || MISSING_GROUPS="$MISSING_GROUPS,$g"
done

echo ""
echo "Setup complete. Run a bag with:"
echo "  $RUN_PREFIX local_orchestrator/run_extraction.py <bag_path>"
if [ -z "$DATA_ROOT_ARG" ]; then
    echo "Shared machine? Re-run with:  $0 --data-root /srv/subdron/data"
fi
if [ -n "$MISSING_GROUPS" ]; then
    echo "Missing groups (needed for shared data / journal access):  sudo usermod -aG ${MISSING_GROUPS#,} $USER"
fi
