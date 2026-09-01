#!/bin/bash
# Per-user setup for the local orchestrator: shared data root, python env,
# local tracking DB. Idempotent — safe to re-run, and safe as a second user on
# a machine that is already set up (the running DB is reused, not recreated).
# The data root is fixed at /srv/subdron/data (override with $DATA_ROOT).
set -e

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
ORCH="$REPO_ROOT/local_orchestrator"
ENV_NAME="pipelines"
DATA_ROOT="${DATA_ROOT:-/srv/subdron/data}"

# Shared data root: the machine's first run creates the group + group-writable
# tree (needs sudo once); later runs just join the current user to the groups.
if [ ! -d "$DATA_ROOT" ] || ! getent group subdron > /dev/null; then
    echo "--- One-time shared data root setup: $DATA_ROOT (sudo) ---"
    sudo groupadd -f subdron
    sudo mkdir -p "$DATA_ROOT"
    sudo chown -R "$USER:subdron" "$DATA_ROOT"
    sudo chmod -R 2775 "$DATA_ROOT"              # setgid: new files inherit the group
    sudo setfacl -R -d -m g::rwx "$DATA_ROOT"    # new files stay group-writable
fi
NEW_GROUPS=""
for g in subdron systemd-journal docker; do
    if getent group "$g" > /dev/null && ! getent group "$g" | grep -qw "$USER"; then
        echo "--- Adding $USER to group '$g' (sudo) ---"
        sudo usermod -aG "$g" "$USER"
        NEW_GROUPS="$NEW_GROUPS $g"
    fi
done

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
    echo "!!! No docker access yet (group changes need a re-login) — log out/in and re-run this script." >&2
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

echo "--- Checking DB logging ---"
DB_POSTGRES_HOST=localhost DB_POSTGRES_PORT=$DB_PORT \
DB_POSTGRES_USER=subdron DB_POSTGRES_PASSWORD=subdron_local \
DB_POSTGRES_DB=mission_data PIPELINE_ENV=dev \
"$PYBIN" -c "
import subdron_utils
subdron_utils.log_mission_stage(mission_id='_db_check', timestamp='2000-01-01-00-00-00', stage='SETUP', status='Done')
" > /dev/null
docker exec subdron-mission-db psql -U subdron -d mission_data -tAc \
    "DELETE FROM missions_test WHERE mission_id = '_db_check';" > /dev/null
echo "    DB write OK"

# Only needed when the DB runs on a non-default port
if [ "$DB_PORT" != "5433" ] && ! grep -q "^export MISSION_DB_PORT=" ~/.bashrc 2>/dev/null; then
    echo "export MISSION_DB_PORT=$DB_PORT" >> ~/.bashrc
    echo "--- Wrote MISSION_DB_PORT=$DB_PORT to ~/.bashrc (new terminals pick it up) ---"
fi

# Install the trigger timer + dashboard once per machine (first user to run setup),
# filling in the per-machine fields from this environment.
if [ ! -f /etc/systemd/system/subdron-pipeline-trigger.service ]; then
    echo "--- Installing systemd units (sudo) ---"
    for f in "$ORCH"/trigger/systemd/*; do
        sed -e "s|^User=.*|User=$USER|" \
            -e "s|^WorkingDirectory=.*|WorkingDirectory=$REPO_ROOT|" \
            -e "s|^ExecStart=[^ ]*python|ExecStart=$PYBIN|" "$f" \
            | sudo tee "/etc/systemd/system/$(basename "$f")" > /dev/null
    done
    sudo systemctl daemon-reload
    sudo systemctl enable --now subdron-pipeline-trigger.timer subdron-pipeline-dashboard
    echo "    trigger runs every minute; dashboard: http://$(hostname):8090"
fi

echo ""
echo "Setup complete. Data root: $DATA_ROOT"
[ -n "$NEW_GROUPS" ] && echo "Group changes ($NEW_GROUPS ) take effect after logging out and back in."
echo "Bags dropped in $DATA_ROOT/<robot>/<mission>/03_Logs/ are processed automatically."
echo "A stage can also be run manually, e.g.:  $RUN_PREFIX local_orchestrator/runners/run_extraction.py <bag_path>"
