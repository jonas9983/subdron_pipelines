# Changelog

All notable changes to this project will be documented in this file.


## [1.2.0] - 2026-08-05

### Added
- **Local orchestrator** (`local_orchestrator/`): fully offline mission processing on a
  single Linux machine, mirroring the cloud pipeline stages.
  - `runners/` — one CLI per stage (`run_extraction.py`, `run_phaseformer.py`,
    `run_colmap.py`). Each validates inputs and bind-mount sources before launching
    Docker, passes mission identity explicitly (parallel-safe outputs), writes a JSON
    job record (`DATA_ROOT/_state/jobs/`, incl. image digest and timings) and the full
    container log (`DATA_ROOT/_state/logs/`). GPU stages serialize on a shared lock.
  - `trigger/pipeline_trigger.py` — level-triggered reconciliation pass (no marker
    files): new bag → extraction; extracted **and human-approved** → PHASEFORMER (per
    approved image range); enhanced → COLMAP. Failed jobs are reported with the last
    error line, never auto-retried.
  - `trigger/approve_bag_for_processing.py` — records the video-review decision
    (approve/reject, optional frame ranges) that gates the GPU stages.
  - `pipeline_trigger.py --reset <stage> <bag>` — redo a bag from any stage: deletes
    that stage's records and outputs plus everything downstream (including the
    Label Studio review task), so the next pass reprocesses from there.
  - Label Studio review integration (`trigger/label_studio_sync.py`, with a one-time
    `setup` subcommand): the trigger creates one review task per extracted bag and
    converts finished annotations (timeline "Process" segments + Approve/Reject)
    into the standard approval files. Active only when `_state/label_studio.json`
    exists; LS outages never block a pass; the CLI approve script stays as fallback.
  - `trigger/systemd/` — service + timer units to run the trigger every minute.
  - `trigger/pipeline_dashboard.py` — live status dashboard on port 8090 (LAN-visible):
    one row per bag, one colored cell per stage (done/running/failed/pending/rejected).
    Running cells show elapsed time, a progress bar and the latest log line; running and
    failed cells link to the full job log (served at `/log/<job_id>`); review cells link
    to the Label Studio task (using the viewer's hostname, so links work from any
    machine); header shows per-state counts and a GPU busy/idle badge. Stdlib HTTP
    server, no new dependencies; own systemd unit. Corrupt job records (e.g. interrupted
    writes) are surfaced as failures instead of crashing the trigger or the dashboard.
  - Local mission-tracking Postgres via `docker-compose.yml` + `init_db.sql`
    (containers log through the same `log_mission_stage` pathway as the cloud;
    host port configurable via `MISSION_DB_PORT`).
  - All tools use `/srv/subdron/data` as the data root (shared, group-writable — the
    same layout on every machine, single- or multi-user); the `DATA_ROOT` env variable
    overrides it for special cases.
  - `scripts/setup_local_orchestrator.sh` — per-user setup: creates the shared data
    root on the machine's first run (sudo) and joins the required groups
    (`subdron`, `docker`, `systemd-journal`); pyenv virtualenv (subdron-utils pinned
    to tag `v1.5.2-dev`, never `latest`); tracking DB (reused if already running,
    port auto-detected — `MISSION_DB_PORT` exported to `~/.bashrc` only when
    non-default).
  - `scripts/update_local_images.sh` + `local_orchestrator/images.env` — stage image
    versions pinned in one file; the script pulls them and points the local `:dev`
    aliases at them.
  - PHASEFORMER model weights are hosted at `DATA_ROOT/_state/models/` and mounted
    into the container, so offline machines need no cloud credentials at runtime.

### Changed
- `.gitignore`: local data tree, virtualenvs and `.python-version` are ignored.
- README split: the cloud pipeline stays in the top-level README; the local
  orchestrator (setup, runners, approval workflow, trigger, dashboard, multi-user)
  is documented in `local_orchestrator/README.md`.
- Runners throttle container progress bars to one line per 10% when stdout is not a
  terminal (keeps journald readable); the per-job log file still gets everything.
- Trigger output reaches the journal immediately (`PYTHONUNBUFFERED=1` in the service
  unit + per-line flush) instead of arriving minutes late in bursts.

### Removed
- `scripts/run_offline_pipeline.sh` and `scripts/test_local_pipeline.sh` — superseded
  by the local orchestrator runners and trigger (offline) and by running the stage
  images directly (cloud-data testing).


## [1.1.2] - 2026-08-03

### Changed
- Aligned release tags with the changelog: seeded tag `v1.1.1` so auto-generated tags continue the 1.x series (next merge produces `v1.1.2-dev`).


## [1.1.1] - 2026-07-31

### Changed
- **Dev functions process all bucket paths**: `process_bag_file_dev`, `process_images_dev` and `process_colmap_dev` no longer ignore files outside `tests/`. The upload base dir (`tests` or `data`) is derived per file from the triggering object's path, so test runs and real mission data coexist on the same bucket.
- `deploy_dev.sh` no longer sets the obsolete `BASE_UPLOAD_DIR` environment variable on the functions.

### Added
- **Automatic dev deployment**: Cloud Build connection and `tag-trigger-pipelines` trigger — merging to `development` now redeploys the dev Cloud Functions automatically (`v*-dev` tag → `cloudbuild.yaml` → `deploy_dev.sh`). Production tags are intentionally not wired up yet.
- `.gitignore` entries for local Terraform artifacts (`.terraform/`, state files).

### Removed
- Decommissioned the misconfigured "prod" Cloud Functions (`process_bag_file`, `process_images`, `process_colmap_prod`): they listened on the `subdron_dev` bucket with dev image tags. Prod redeployment against `subdron_prod` is planned separately.


## [1.1.0] - 2026-07-27

### Added
- **Offline local pipeline**: Added script `/scripts/run_offline_pipeline.sh` to run the full pipeline locally without Cloud or database dependencies.


## [1.0.1] - 2026-06-02

### Added
- **CI/CD Automation**: Implemented automated environment-based tagging and deployment support.
  - Added `bitbucket-pipelines.yml` to automatically generate version tags when merging to `development` (`v*-dev`) and `master` (`v*`).
  - Added `cloudbuild.yaml` configuration to trigger development or production deployments based on the generated git tags.

### Changed
- **Triggers**: Updated trigger configurations for production batch jobs.

## [1.0.0] - 2026-04-23

### Added
- **GCP Batch Integration**: Orchestrated the transition from manual processing to scalable Google Cloud Batch jobs.
- **Automated Trigger System**: Implemented marker-based sequencing using Cloud Functions:
    - `.bag` upload triggers `rosbag_extractor`.
    - `EXTRACTION_COMPLETE.marker` triggers `image_processing`.
    - `ENHANCEMENT_COMPLETE.marker` triggers `colmap_processing`.
- **Environment Management**: Split deployment logic into `dev` and `prod` environments with isolated configurations.
- **Dynamic Tagging**: Added support for `TARGET_IMAGE_TAG` to allow testing specific container versions in `dev` without affecting `prod`.
- **GPU Acceleration**: Configured Batch Jobs to automatically provision NVIDIA Tesla T4 GPUs for intensive tasks like `PHASEFORMER`.
- **Security**: Integrated Google Cloud Secret Manager. The secret `DB_URL` is now automatically injected as the `DATABASE_URL` environment variable into processing containers.
- **Video Generation**: Standardized video output flags across pipeline stages, including configurable FPS (default 30).
- **Spatial Matching**: Added support for spatial matching strategies in `colmap_processing` based on navigation data.

### Changed
- **Architecture**: Moved processing logic out of Cloud Functions into specialized Docker containers managed by Batch API.
- **Deployment**: Unified deployment workflow via `deploy.sh` for multi-stage verification.
- **Path Normalization**: Standardized `gs://` path handling across all Cloud Functions.
