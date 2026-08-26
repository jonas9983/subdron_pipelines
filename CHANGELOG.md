# Changelog

All notable changes to this project will be documented in this file.


## [1.3.0] - 2026-08-26

- Anomaly detection stage: `runners/run_anomaly.py` scores extracted images at 1 fps through the always-on anomaly API (`anomaly-api` service in `local_orchestrator/docker-compose.yml`); results in `camera/<ts>/anomaly/<model>/`, weights hosted at `_state/models/anomaly/`.
- The trigger launches anomaly detection in the background right after extraction, independent of the review gate; `--reset anomaly` redoes only that stage.
- Job ids use single underscores between fields (existing `__` records need renaming).


## [1.2.0] - 2026-08-05

- Local orchestrator (`local_orchestrator/`): fully offline mission processing mirroring the cloud stages.
- One runner CLI per stage (extraction, PHASEFORMER, COLMAP): input/mount validation, JSON job records in `DATA_ROOT/_state/jobs/`, full logs in `_state/logs/`, shared GPU lock.
- `pipeline_trigger.py`: level-triggered pass (new bag -> extraction; approved -> PHASEFORMER per range; enhanced -> COLMAP); failed jobs reported, never auto-retried; `--reset <stage> <bag>` redoes a bag from any stage.
- Review gate via Label Studio (`label_studio_sync.py`, one task per bag, Approve/Reject + frame ranges) with `approve_bag_for_processing.py` as CLI fallback; systemd service + timer run the trigger every minute.
- Status dashboard on port 8090 (per-bag stage cells, progress, log links, GPU badge); local mission-tracking Postgres via docker-compose.
- Shared data root `/srv/subdron/data`; `setup_local_orchestrator.sh` per-user setup; stage image versions pinned in `images.env`; PHASEFORMER weights hosted at `_state/models/` (no cloud credentials at runtime).
- Removed `run_offline_pipeline.sh` and `test_local_pipeline.sh` (superseded).


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
