# Local Orchestrator (offline processing)

For machines with no Google Cloud access, `local_orchestrator/` runs pipeline stages
fully offline against a shared local data tree at **`/srv/subdron/data`** and a local
Postgres for mission tracking (same `log_mission_stage` pathway the cloud services use).

One-time setup per user — creates the shared data root on the machine's first run
(sudo), joins the required groups, builds the pyenv virtualenv `pipelines` (or `.venv`
fallback) with subdron-utils pinned to a release tag, and starts (or adopts) the
tracking DB:

```bash
./scripts/setup_local_orchestrator.sh
```

## Stage runners (`local_orchestrator/runners/`)

One script per stage, one bag per invocation:

```bash
python local_orchestrator/runners/run_extraction.py  /srv/subdron/data/sparus2-25/P1063/03_Logs/<bag>.bag
python local_orchestrator/runners/run_phaseformer.py /srv/subdron/data/sparus2-25/P1063/02_Post_Mission/camera/<ts>/image_pose_pairs
python local_orchestrator/runners/run_colmap.py      /srv/subdron/data/sparus2-25/P1063/02_Post_Mission/camera/<ts>/enhanced/PHASEFORMER
```

Every runner validates inputs and mounts before launching Docker, writes a job record
to `DATA_ROOT/_state/jobs/<job_id>.json` and the full container log to
`DATA_ROOT/_state/logs/`. GPU stages share a lock so only one runs at a time.
Common flags: `--tag` (image tag, default `dev`), `--force` (re-run a completed job),
`--dev-code` (bind-mount local checkouts for iterating).

## Automation (`local_orchestrator/trigger/`)

`pipeline_trigger.py` makes one pass over the data tree and launches each bag's next
stage, reading state from the job records: new bag → extraction; extracted **and
approved** → PHASEFORMER (per approved image range); enhanced → COLMAP. Failed jobs
are reported, never auto-retried.

The approval gate: after extraction each bag waits for a human decision. With the
Label Studio integration active (below), the trigger creates one review task per
extracted bag — watch the video there and mark the segments worth processing on the
timeline ("Process" label). Segments alone are a draft: **nothing runs until the
Approve choice is selected and the annotation submitted** (Reject skips the GPU
stages). The next trigger pass then converts it into the approval — timeline frames
map 1:1 to image indices, since the videos are rendered at 30 fps from the extracted
images. The CLI fallback records the same decision by hand:

```bash
python local_orchestrator/trigger/approve_bag_for_processing.py /srv/subdron/data/sparus2-25/P1063/03_Logs/<bag>.bag --ranges 0:500 1400:2100
# or: --reject
```

### Label Studio review setup (one-time per machine)

1. In the Label Studio compose stack: pin a current image (replace `build: .` +
   `image: ...:latest` with `image: heartexlabs/label-studio:1.23.0` on every
   service using it), make sure the app service has
   `LABEL_STUDIO_LOCAL_FILES_SERVING_ENABLED=true`, and mount the data root
   read-only under its `LABEL_STUDIO_LOCAL_FILES_DOCUMENT_ROOT`, e.g. with
   document root `/label-studio`:
   ```yaml
   volumes:
     - /srv/subdron/data:/label-studio/subdron_data:ro
   ```
   Back up its DB, then update:
   ```bash
   docker compose exec db pg_dump -U postgres postgres > ls_backup.sql
   docker compose pull && docker compose up -d
   ```
2. Get an API token from the LS UI (Account & Settings → Access Token). On LS ≥ 1.17
   the organization setting "Legacy Tokens" must be enabled
   (Organization → API Tokens Settings) for `Token` auth — installations upgraded
   from older versions already have it on.
3. Connect the pipeline (creates the "SUBDRON bag review" project and saves the
   connection to `_state/label_studio.json`). `--video-prefix` is the data root's
   path inside the LS document root — for the mount above:
   ```bash
   python local_orchestrator/trigger/label_studio_sync.py setup \
       --url http://localhost:8080 --video-prefix subdron_data
   ```
   The token is read from the `LABEL_STUDIO_TOKEN` environment variable
   (or passed explicitly with `--token`).

From then on the trigger and the dashboard show "in review (task N)" for bags with
an open task. Removing `_state/label_studio.json` disables the integration; the
pipeline never depends on Label Studio being up (failures are logged and skipped).

To run the trigger automatically every minute, install the systemd units from
`local_orchestrator/trigger/systemd/` (edit paths/user in the .service first):

```bash
sudo cp local_orchestrator/trigger/systemd/subdron-pipeline-trigger.* /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now subdron-pipeline-trigger.timer
journalctl -u subdron-pipeline-trigger -f          # watch trigger live
python local_orchestrator/trigger/pipeline_trigger.py --dry-run   # status of every bag
```

## Monitoring (`local_orchestrator/trigger/pipeline_dashboard.py`)

A live status dashboard — one row per bag, one colored cell per stage
(green done · yellow running · red failed · blue pending · grey rejected).
Running cells show elapsed time and the latest progress line; failed cells show
the last error and the log path. Rendered from the job records on every request
(auto-refreshes every 10 s), reachable from any machine on the LAN:

```bash
sudo cp local_orchestrator/trigger/systemd/subdron-pipeline-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now subdron-pipeline-dashboard
# open http://<machine>:8090   (port: PIPELINE_DASHBOARD_PORT)
```

## Environment & machine prerequisites

| What | How |
| :--- | :--- |
| Data root | `/srv/subdron/data` (the `DATA_ROOT` env variable overrides it for special cases). Bags go in `/srv/subdron/data/<robot>/<mission>/03_Logs/*.bag`. |
| Tracking DB port | Default 5433. `MISSION_DB_PORT` is only needed when the DB runs on another port — the setup script detects this and writes the export to `~/.bashrc`. |
| Docker images | Versions are pinned in `local_orchestrator/images.env` (edit + commit to upgrade). Apply on any machine with `./scripts/update_local_images.sh` — it pulls the pinned versions and points the local `:dev` aliases at them. Job records store the digest of every run. |
| PHASEFORMER weights | One-time: `gcloud storage cp gs://subdron_dev/machine_learning/image_processing/weights/best.pth /srv/subdron/data/_state/models/PHASEFORMER/` (the runner prints this command if the weights are missing). |
| Failures | The trigger never retries. Fix the cause (see the log path it prints), then re-run the stage with `--force`. |

## Multi-user operation

The data root is shared and group-writable by design, so several users can work on
the same machine from their own accounts. The trigger is a **system** service: it
runs as one account regardless of who is logged in, and any user can check it
(`systemctl status subdron-pipeline-trigger.timer`) or read its logs. Onboarding a
new user is just `./scripts/setup_local_orchestrator.sh` from their own clone — the
script joins the required groups (`subdron`, `docker`, `systemd-journal`) and reuses
the machine's running DB. Code is per-user; only the data directory is shared, and
the trigger already runs machine-wide, so there is nothing to start.

---

