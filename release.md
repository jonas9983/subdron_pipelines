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
| Compose services | `local_orchestrator/docker-compose.yml` | `anomaly-api` (always-on anomaly scoring service, CPU inference through OpenVINO). The mission tracking DB is the `subdron_missions_database` stack on the same host. |
| Image pins | `local_orchestrator/images.env`, `scripts/update_local_images.sh` | Versions of the stage images; the script pulls them and points the local `:dev` aliases at them. |
| Setup | `subdron_missions_database/setup_host.sh` (sibling repository) | The single host setup: mission stack, data root, this repo's `.venv`, DB connection, systemd units. |
| systemd units | `local_orchestrator/trigger/systemd/` | `subdron-pipeline-trigger.service` + `.timer` (one pass per minute), `subdron-pipeline-dashboard.service`. |

### Processing flow

```
bag in 03_Logs ──► EXTRACTION ──┬──► ANOMALY (CPU, seconds, independent of review)
                                │
                                └──► REVIEW (Label Studio) ──► PHASEFORMER ──► COLMAP
```

1. **Extraction** (`subdron-rosbag-extractor`): transforms navigation into camera
   poses, extracts images, writes `image_pose_pairs/`, navigation CSVs and the review
   videos (`clean_30FPS.mp4`, overlaid variant). Updates the mission-tracking DB.
2. **Anomaly detection** (`anomaly-api`, image `subdron-anomaly-api` from the
   `subdron-anomaly` registry repository): as soon as a bag is extracted, the trigger
   runs the anomaly runner. It subsamples the images to 1 frame per
   second of footage (`--fps`, using the timestamps in the file names), posts each
   frame to the API's `/predict` endpoint and writes `scores.csv` plus, for each
   anomalous frame, an image with the original next to the heatmap overlay and the score,
   to `02_Post_Mission/results/anomaly/<ts>/<model>/`. Default model `padim`
   (also `patchcore`, `stfpm`, `efficientad`); about 45 ms per frame on the CPU, so a
   bag is scored in seconds. The result is informative for the reviewer and never
   blocks anything.
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
   so the local runner sets `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`,
   verifies every expected output after the run, resumes the missing images with a
   halved batch size (8 → 4 → 2 → 1), and marks the job failed if any are still missing.
5. **COLMAP** (`subdron-colmap`): spatial matching strategy over the enhanced images and
   the extracted poses; results in `results/colmap/runs/<ts>/PHASEFORMER_spatial/`.

### State model

All state is on disk under `DATA_ROOT/_state/`, so any process (trigger, dashboard,
a user's shell) sees the same truth and the machine can reboot at any time.

| Path | Content |
| :--- | :--- |
| `jobs/<job_id>.json` | Job record: stage, inputs, image digest, outputs, `runner_pid`, `status` (`running` / `done` / `failed`), timestamps, duration, exit code, `error_tail`. |
| `logs/<job_id>.log` | Full container output (or, for anomaly, the runner's per-frame progress and JSON summary). |
| `approvals/<job_id>.json` | The review decision: `approved`, `ranges`, `decided_utc`, `source` (`label-studio` or CLI). |
| `ls_tasks/<job_id>.json` | Label Studio task id created for the bag. |
| `label_studio.json` | Label Studio connection (URL, token, project id, video prefix). Owner and group readable only. |
| `mission_db.env` | Mission DB connection for the runners (host, port, user, password, db). Owner and group readable only. |
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
| extracted, no anomaly record, API reachable | run anomaly detection (seconds) |
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

Prerequisites: Ubuntu with Docker, `sudo` for the operator account, and the
`subdron_missions_database` repository checked out next to this one.

1. Run the host setup once, as the operator account:
   ```bash
   ../subdron_missions_database/setup_host.sh
   ```
   It installs the system extras (NVIDIA container toolkit, `jq`, `acl`, `python3-venv`),
   starts Postgres, MLflow, Metabase and Label Studio, creates `/srv/subdron/data`
   (group `subdron`, setgid, default ACLs so new files stay group-writable), builds this
   repository's `.venv` with `subdron-utils` pinned to a release tag, stores the mission-DB
   connection in `_state/mission_db.env`, and installs the systemd units (trigger timer +
   dashboard) running as that account from this checkout. It is idempotent.
2. Pull the pinned stage images (needs the machine's service account active in gcloud):
   ```bash
   ./scripts/update_local_images.sh
   ```
3. Place the PHASEFORMER weights:
   ```bash
   gcloud storage cp gs://subdron_dev/machine_learning/image_processing/weights/best.pth /srv/subdron/data/_state/models/PHASEFORMER/
   ```
4. Anomaly weights (OpenVINO export, per model):
   ```bash
   for M in padim patchcore stfpm efficientad; do
     gcloud storage cp gs://subdron_dev/machine_learning/anomaly_detection/weights/$M/model.xml \
                       gs://subdron_dev/machine_learning/anomaly_detection/weights/$M/model.bin \
                       /srv/subdron/data/_state/models/anomaly/$M/
   done
   ```
   The anomaly API image is pulled by step 2 and the service started by it (and by the
   host setup once the image exists); it restarts with Docker from then on.
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
| `DB_POSTGRES_HOST/PORT/USER/PASSWORD/DB` | `host.docker.internal:5432`, `mission_admin`, `mission_data` | Mission DB connection. Read from `DATA_ROOT/_state/mission_db.env` (written by the host setup); environment variables override. |
| `PIPELINE_DASHBOARD_PORT` | `8090` | Dashboard port. |
| `ANOMALY_API_URL` | `http://localhost:8095` | Where the trigger, runner and dashboard look for the anomaly API. |
| `ANOMALY_API_PORT` | `8095` | Host port published by the compose service. |
| `LABEL_STUDIO_TOKEN` | — | Token for `label_studio_sync.py setup`. |
| `local_orchestrator/images.env` | — | Pinned image versions (`EXTRACTOR_VERSION`, `IMAGE_PROCESSING_VERSION`, `COLMAP_VERSION`, `ANOMALY_API_VERSION`). |
| Runner flags | `--tag dev`, `--dev-code` | Image tag to run; bind-mount local checkouts for iterating on stage code (development only; the installed trigger unit runs the pinned image). |

Tracking DB: the `subdron_missions_database` Postgres (`mission_data`, user
`mission_admin`), reached from the containers as `host.docker.internal:5432`; stages log
through the same `log_mission_stage` pathway as the cloud services did, into `missions_test`
(`PIPELINE_ENV=dev`).

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

One operator account owns the machine, the checkouts and the services; the trigger and
dashboard run as that account regardless of who is logged in. Everyone else only drops
bags into the shared, group-writable data root and reviews in Label Studio or watches the
dashboard. A local account that must write to the data root is added to group `subdron`.

## 7. Design decisions

- **Level-triggered, file-based state.** The trigger recomputes every bag's next action
  from the records on each pass instead of keeping a queue, so crashes, reboots and
  manual interventions (deleting a record, dropping a bag) are handled uniformly.
- **No automatic retries.** A failed stage stays failed and visible until a person
  resets it; this keeps a broken bag from consuming the GPU every minute.
- **Explicit Approve.** Segments alone never start processing, so a reviewer can save
  a draft without triggering GPU work.
- **Anomaly as a side branch.** It depends only on extraction, runs through an
  always-on CPU API (model kept in memory, no per-bag container start, seconds per bag),
  and its failure or absence never blocks the main flow.
- **Pinned images, digests in records.** Every job record stores the image digest it
  ran with, so results are reproducible and upgrades are explicit (`images.env`).
- **Label Studio is optional.** If it is down or unconfigured the pipeline continues,
  and the CLI approval writes the same approval file.

## 8. Known limitations and open items

- The anomaly API source lives outside this repository (`subdron_anomaly_detection`);
  the published `dev` image is the cloud variant, whose batch endpoint reads GCS URIs,
  so the local runner uses the single-image `/predict` endpoint frame by frame.
- Anomaly inference is CPU-only (OpenVINO); the `.pt` weights in the bucket were
  exported on GPU and cannot be loaded without one.
- The dashboard does not reload code by itself; a pull without a restart leaves it
  rendering stale logic.
- A runner killed mid-job leaves its record in `running` until someone resets the stage.
- An 8 GB desktop GPU (RTX 4060) enhances 720p images at roughly 2 images/s; a
  3200-image bag takes about half an hour. Batch size 8 is borderline there, and
  desktop applications sharing the card can push single batches into out-of-memory,
  which the runner's retry then fills in.
- Job records written before 1.3.0 (double-underscore ids) are ignored, not migrated.
- Single machine, single GPU; there is no scheduling across machines.

## 9. Changes in this release

See `CHANGELOG.md`, entries 1.2.0 (initial local orchestrator) and 1.3.0 (anomaly
stage, single-underscore ids, anomaly API health handling, dashboard ANOMALY column).
