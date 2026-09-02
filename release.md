# SUBDRON Offline Pipeline (Local Orchestrator) — Release Notes

Temporary source for the Confluence release page. Everything below describes the
`local_orchestrator/` component of `subdron_pipelines` as of version 1.3.0
(2026-09-02). Day-to-day operations and troubleshooting are in
`local_orchestrator/README.md`.

## 1. Purpose and scope

The offline pipeline runs the SUBDRON post-mission processing stages on a single
machine with no Google Cloud access, for example a laptop or workstation in the field
that receives mission bags on a pen drive. It reuses the same stage containers as the
cloud pipeline (rosbag extractor, PHASEFORMER image enhancement, COLMAP reconstruction)
and the same mission-tracking database schema, and adds three things the cloud does not
have:

- automatic triggering from a watched data tree, without any cloud queue;
- a human review gate between extraction and the GPU stages;
- a live status dashboard reachable from the LAN.

Out of scope: cloud upload, multi-machine scheduling, automatic retries.

## 2. Architecture

| Component | File(s) | Role |
| :--- | :--- | :--- |
| Stage runners | `local_orchestrator/runners/run_extraction.py`, `run_phaseformer.py`, `run_colmap.py`, `run_anomaly.py` | One CLI per stage, one bag per invocation. Validate inputs and bind mounts, launch the pinned Docker image (or call the anomaly API), stream the log, write the job record. |
| Shared helpers | `local_orchestrator/pipeline_helpers.py` | Data root resolution, job record writing, Docker command construction, GPU lock, anomaly API health check, tracking-DB environment. |
| Trigger | `local_orchestrator/trigger/pipeline_trigger.py` | One level-triggered pass over the data tree: launches each bag's next missing stage. Run every minute by a systemd timer. Also hosts `--reset`. |
| Review sync | `local_orchestrator/trigger/label_studio_sync.py` | Creates one Label Studio task per extracted bag and converts submitted annotations into approval files. |
| CLI approval | `local_orchestrator/trigger/approve_bag_for_processing.py` | Manual fallback that writes the same approval file. |
| Dashboard | `local_orchestrator/trigger/pipeline_dashboard.py` | HTTP server on port 8090 rendering one row per bag and one cell per stage from the job records; serves job logs. |
| Compose services | `local_orchestrator/docker-compose.yml` | `mission-db` (Postgres 16, mission tracking) and `anomaly-api` (always-on anomaly scoring service with GPU). |
| Image pins | `local_orchestrator/images.env`, `scripts/update_local_images.sh` | Versions of the stage images; the script pulls them and points the local `:dev` aliases at them. |
| Setup | `scripts/setup_local_orchestrator.sh` | Per-user one-time setup; installs the systemd units the first time it runs on a machine. |
| systemd units | `local_orchestrator/trigger/systemd/` | `subdron-pipeline-trigger.service` + `.timer` (one pass per minute), `subdron-pipeline-dashboard.service`. |

### Processing flow

```
bag in 03_Logs ──► EXTRACTION ──┬──► ANOMALY (background, independent of review)
                                │
                                └──► REVIEW (Label Studio) ──► PHASEFORMER ──► COLMAP
```

1. **Extraction** (`subdron-rosbag-extractor`): transforms navigation into camera
   poses, extracts images, writes `image_pose_pairs/`, navigation CSVs and the review
   videos (`clean_30FPS.mp4`, overlaid variant). Updates the mission-tracking DB.
2. **Anomaly detection** (`anomaly-api`): as soon as a bag is extracted, the trigger
   launches the runner in the background. It subsamples the images to 1 frame per
   second of footage (`--fps`) and posts the directory to the API, which writes
   `scores.csv` and heatmaps of the anomalous frames to `camera/<ts>/anomaly/<model>/`.
   The result is informative for the reviewer and never blocks anything.
3. **Review**: one Label Studio task per bag shows the clean review video. The reviewer
   marks the segments worth processing on the timeline ("Process" label) and submits
   the Approve or Reject choice. Timeline frames correspond to images one to one
   because the videos are rendered at 30 fps from the extracted images; the sync
   converts the 1-based inclusive frame ranges into the stage's 0-based slices.
   Segments without an explicit Approve are a draft and start nothing. Reject skips
   the GPU stages.
4. **PHASEFORMER** (`subdron-image-processing`): one run per approved range
   (`--image-range start:end:step`), later ranges resume into the same output
   directory. Weights come from `_state/models/PHASEFORMER/best.pth`. The stage skips
   a batch that fails (typically CUDA out of memory on small GPUs) and still exits 0,
   so the local runner picks the batch size from the GPU memory (2 / 4 / 8 for
   < 10 GB / < 16 GB / larger), sets `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`,
   verifies every expected output after the run, resumes the missing images with a
   halved batch, and marks the job failed if any are still missing.
5. **COLMAP** (`subdron-colmap`): spatial matching strategy over the enhanced images and
   the extracted poses; results in `results/colmap/runs/<ts>/PHASEFORMER_spatial/`.

### State model

All state is on disk under `DATA_ROOT/_state/`, so any process (trigger, dashboard,
a user's shell) sees the same truth and the machine can reboot at any time.

| Path | Content |
| :--- | :--- |
| `jobs/<job_id>.json` | Job record: stage, inputs, image digest, outputs, `runner_pid`, `status` (`running` / `done` / `failed`), timestamps, duration, exit code, `error_tail`. |
| `logs/<job_id>.log` | Full container or API output. Anomaly runs also have `<job_id>_anomaly.launch.log` with the runner's own output. |
| `approvals/<job_id>.json` | The review decision: `approved`, `ranges`, `decided_utc`, `source` (`label-studio` or CLI). |
| `ls_tasks/<job_id>.json` | Label Studio task id created for the bag. |
| `label_studio.json` | Label Studio connection (URL, token, project id, video prefix). Owner and group readable only. |
| `models/` | PHASEFORMER and anomaly weights, mounted read-only into the containers. |
| `gpu.lock`, `trigger.lock` | `flock` files: one GPU job at a time, one trigger pass at a time. |

Job ids are `<robot>_<mission>_<timestamp>` with the stage suffix `_anomaly`,
`_phaseformer` or `_colmap-spatial`. Versions before 1.3.0 used double underscores;
records written by them are not recognised and must be renamed or reset.

The trigger's decision table, evaluated per bag on every pass:

| Observed state | Action |
| :--- | :--- |
| no extraction record | run extraction |
| extraction failed / running | report, do nothing |
| extracted, no anomaly record, API reachable | launch anomaly in the background |
| extracted, no approval, Label Studio configured | create the task if missing, else read its annotation |
| extracted, approval says rejected | stop |
| approved, no PHASEFORMER record | run PHASEFORMER per range |
| enhanced, no COLMAP record | run COLMAP |
| COLMAP done | report complete |

Only one stage runs per pass and passes are serial, so a long extraction simply delays
the next pass. Failed stages are never retried automatically; `--reset <stage> <bag>`
deletes a stage's records and outputs (plus everything downstream, anomaly excepted)
so the next pass redoes it.

## 3. Installation on a new machine

Prerequisites: Ubuntu with Docker, the NVIDIA container runtime, `pyenv`, `sudo`
for the first user, and the stage images available (registry access once, or loaded
from a tarball).

1. Clone the repository and run, as each user who will operate the pipeline:
   ```bash
   ./scripts/setup_local_orchestrator.sh
   ```
   The script creates `/srv/subdron/data` (group `subdron`, setgid, default ACLs so
   new files stay group-writable), adds the user to `subdron`, `docker` and
   `systemd-journal`, builds the `pipelines` pyenv virtualenv with `subdron-utils`
   pinned to a release tag, starts or adopts the tracking DB (`docker compose`,
   port 5433 unless `MISSION_DB_PORT` is set or a DB container is already running on
   another port, in which case the port is written to `~/.bashrc`), and on the first
   run per machine
   installs the systemd units with the user, repository path and interpreter filled in.
2. Pull the pinned stage images:
   ```bash
   ./scripts/update_local_images.sh
   ```
3. Place the PHASEFORMER weights:
   ```bash
   gcloud storage cp gs://subdron_dev/machine_learning/image_processing/weights/best.pth /srv/subdron/data/_state/models/PHASEFORMER/
   ```
4. Anomaly service (optional until the image is distributed): load the image
   `subdron-anomaly-api:<ANOMALY_API_VERSION>`, place the weights at
   `_state/models/anomaly/<model>/model.ckpt`, then
   `docker compose -f local_orchestrator/docker-compose.yml up -d anomaly-api`.
   The service restarts with Docker from then on.
5. Connect Label Studio (next section).

### Label Studio setup (once per machine)

1. In the Label Studio compose stack, pin a current image (replace `build: .` +
   `image: ...:latest` with `image: heartexlabs/label-studio:1.23.0` on every service
   using it), set `LABEL_STUDIO_LOCAL_FILES_SERVING_ENABLED=true` on the app service, and
   mount the data root read-only under its `LABEL_STUDIO_LOCAL_FILES_DOCUMENT_ROOT`,
   for example with document root `/label-studio`:
   ```yaml
   volumes:
     - /srv/subdron/data:/label-studio/subdron_data:ro
   ```
   Back up its DB, then update:
   ```bash
   docker compose exec db pg_dump -U postgres postgres > ls_backup.sql
   docker compose pull && docker compose up -d
   ```
2. Get an API token from the UI (Account & Settings → Access Token). On Label Studio
   1.17 or newer, "Legacy Tokens" must be enabled under Organization → API Tokens
   Settings for `Token` authentication.
3. Create the review project and save the connection (`--video-prefix` is the data
   root's path inside the document root):
   ```bash
   LABEL_STUDIO_TOKEN=<token> python local_orchestrator/trigger/label_studio_sync.py setup \
       --url http://localhost:8080 --video-prefix subdron_data
   ```
   This creates the project "SUBDRON bag review" with the Approve/Reject choices and the
   "Process" timeline label, registers the local storage, and writes
   `_state/label_studio.json`. Removing that file disables the integration.

## 4. Configuration reference

| Variable / file | Default | Meaning |
| :--- | :--- | :--- |
| `DATA_ROOT` | `/srv/subdron/data` | Shared data root. |
| `MISSION_DB_PORT` | `5433` | Host port of the tracking DB. Set in the trigger unit and `~/.bashrc` by the setup script when non-default. |
| `PIPELINE_DASHBOARD_PORT` | `8090` | Dashboard port. |
| `ANOMALY_API_URL` | `http://localhost:8095` | Where the trigger, runner and dashboard look for the anomaly API. |
| `ANOMALY_API_PORT` | `8095` | Host port published by the compose service. |
| `LABEL_STUDIO_TOKEN` | — | Token for `label_studio_sync.py setup`. |
| `local_orchestrator/images.env` | — | Pinned image versions (`EXTRACTOR_VERSION`, `IMAGE_PROCESSING_VERSION`, `COLMAP_VERSION`, `ANOMALY_API_VERSION`). |
| Runner flags | `--tag dev`, `--dev-code` | Image tag to run; bind-mount local checkouts for iterating on stage code. The installed trigger unit runs `--dev-code extraction`. |

Tracking DB: Postgres `mission_data`, user `subdron`, reached from the containers as
`host.docker.internal:<MISSION_DB_PORT>`; stages log through the same
`log_mission_stage` pathway as the cloud services.

## 5. Monitoring

The dashboard (`http://<machine>:8090`) renders on every request from the job records
and auto-refreshes every 10 s. Columns: MISSION, TIMESTAMP, EXTRACT, ANOMALY, REVIEW,
ENHANCE, COLMAP. Cell colours: green done, yellow running (with elapsed time, latest
progress line and a progress bar), red failed (last error line, link to the log), blue
pending, grey rejected. The REVIEW cell links to the bag's Label Studio task using the
viewer's hostname, so it works from any LAN machine. The ANOMALY cell shows "api down"
while the anomaly service is unreachable and the anomalous-frame count once scored;
it never affects the bag's summary state. A GPU badge shows whether a GPU stage is
running.

The dashboard loads code at start; after a `git pull` it must be restarted
(`sudo systemctl restart subdron-pipeline-dashboard`). The trigger is a one-shot
service and always runs the current code.

## 6. Multi-user operation

The data root is shared and group-writable by design. The trigger and dashboard are
system services running as one account regardless of who is logged in; any user in
`systemd-journal` can read their logs. Onboarding a user is running the setup script
from their own clone; code is per user, data and services are per machine.

## 7. Design decisions

- **Level-triggered, file-based state.** The trigger recomputes every bag's next action
  from the records on each pass instead of keeping a queue, so crashes, reboots and
  manual interventions (deleting a record, dropping a bag) are handled uniformly.
- **No automatic retries.** A failed stage stays failed and visible until a person
  resets it; this keeps a broken bag from consuming the GPU every minute.
- **Explicit Approve.** Segments alone never start processing, so a reviewer can save
  a draft without triggering GPU work.
- **Anomaly as a side branch.** It depends only on extraction, runs in the background
  through an always-on API (model kept in memory, no per-bag container start), and its
  failure or absence never blocks the main flow.
- **Pinned images, digests in records.** Every job record stores the image digest it
  ran with, so results are reproducible and upgrades are explicit (`images.env`).
- **Label Studio is optional.** If it is down or unconfigured the pipeline continues,
  and the CLI approval writes the same approval file.

## 8. Known limitations and open items

- The anomaly API image is not yet handled by `update_local_images.sh` and has no
  Dockerfile in this repository; it must be provided separately along with the weights.
- The dashboard does not reload code by itself; a pull without a restart leaves it
  rendering stale logic.
- A runner killed mid-job leaves its record in `running` until someone resets the stage.
- An 8 GB desktop GPU (RTX 4060) enhances 720p images at roughly 2 images/s with batch
  size 2 to 4; a 3200-image bag takes about half an hour. Desktop applications sharing
  the card reduce the available memory.
- Job records written before 1.3.0 (double-underscore ids) are ignored, not migrated.
- Single machine, single GPU; there is no scheduling across machines.

## 9. Changes in this release

See `CHANGELOG.md`, entries 1.2.0 (initial local orchestrator) and 1.3.0 (anomaly
stage, single-underscore ids, anomaly API health handling, dashboard ANOMALY column).
