# Changelog

All notable changes to this project will be documented in this file.


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
