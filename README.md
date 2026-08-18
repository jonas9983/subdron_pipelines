# Subdron Underwater Vision Pipeline

This repository orchestrates the end-to-end processing pipeline for underwater imagery, from raw ROS bags to 3D reconstructions. It manages the deployment of Cloud Functions that trigger specialized Google Cloud Batch Jobs.


## 1. Prerequisites

Before setting up the pipeline, the following requirements must be met:

*   **Directory Structure**: All related repositories (`subdron_camera_transformation`, `subdron_image_processing`, `subdron_colmap`, and `subdron-utils`) must be located in the same parent directory as this repository for the local scripts and build processes to work.
*   **Google Cloud SDK**: `gcloud` authenticated and configured to the correct project.
*   **Artifact Registry**: Access to the Subdron Artifact Registry.
*   **Docker**: Docker and Docker Compose installed and running.
*   **Secrets**: Secret Manager access for the `DB_URL` secret.


## 2. Core Architecture

### Workflow Diagram

*(Note: An architectural diagram will be added here soon.)*

### Event-Driven Logic

The pipeline is event-driven and uses marker files to sequence processing stages across Google Cloud Batch:

1.  **Rosbag Extractor**: Triggered by `.bag` file uploads to GCS. Extracts images and navigation data.
2.  **Image Processing**: Triggered by the `EXTRACTION_COMPLETE.marker`. Enhances image quality (PHASEFORMER/CLAHE).
3.  **Colmap Processing**: Triggered by the `ENHANCEMENT_COMPLETE.marker`. Performs 3D reconstruction and spatial matching.

## 3. Key Features

*   **Automated Sequencing**: Uses GCS Object events and JSON marker files to pass metadata between stages.
*   **Scalable Processing**: Uses GCP Batch Jobs for data processing with automatic resource provisioning.
*   **GPU Support**: Integrated NVIDIA T4 GPU support for deep-learning-based enhancement (PHASEFORMER).
*   **Security**: The GCP secret `DB_URL` is injected as the `DATABASE_URL` environment variable into processing containers.
*   **Environment Isolation**: Distinct `dev` and `prod` configurations for safe testing and stable production runs.
*   **Dynamic Tagging**: Control container versions via `TARGET_IMAGE_TAG` environment variable in Cloud Functions.


## 4. Pipeline Organization

### Directory Structure

- `rosbag_extractor/`: Cloud Function code to trigger the `subdron_camera_transformation` Extractor batch job.
- `image_processing/`: Cloud Function code to trigger the `subdron_image_processing` batch job.
- `colmap_processing/`: Cloud Function code to trigger the `subdron_colmap` reconstruction batch job.
- `scripts/`: Centralized scripts for building, pushing, and deploying the entire pipeline.

### Environment Workflow

#### Development (`dev`)
- **Bucket Path**: `subdron_dev/tests/.../03_Logs/test_bags`
- **Container Tags**: Defaults to `dev`. Can be overridden with `TARGET_IMAGE_TAG`.
- **Purpose**: Rapid testing of new algorithms and matching strategies.

#### Production (`prod`)
- **Bucket Path**: `subdron_dev/data/`
- **Container Tags**: Defaults to `production`.
- **Purpose**: Stable, finalized pipeline for mission data.

## 5. Local Orchestrator (offline processing)

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

### Stage runners (`local_orchestrator/runners/`)

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

### Automation (`local_orchestrator/trigger/`)

`pipeline_trigger.py` makes one pass over the data tree and launches each bag's next
stage, reading state from the job records: new bag → extraction; extracted **and
approved** → PHASEFORMER (per approved image range); enhanced → COLMAP. Failed jobs
are reported, never auto-retried.

The approval gate: after extraction, review the videos in
`02_Post_Mission/camera/videos/<ts>/original/`, then record the decision:

```bash
python local_orchestrator/trigger/approve_bag_for_processing.py /srv/subdron/data/sparus2-25/P1063/03_Logs/<bag>.bag --ranges 0:500 1400:2100
# or: --reject
```

To run the trigger automatically every minute, install the systemd units from
`local_orchestrator/trigger/systemd/` (edit paths/user in the .service first):

```bash
sudo cp local_orchestrator/trigger/systemd/subdron-pipeline-trigger.* /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now subdron-pipeline-trigger.timer
journalctl -u subdron-pipeline-trigger -f          # watch trigger live
python local_orchestrator/trigger/pipeline_trigger.py --dry-run   # status of every bag
```

### Monitoring (`local_orchestrator/trigger/pipeline_dashboard.py`)

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

### Environment & machine prerequisites

| What | How |
| :--- | :--- |
| Data root | `/srv/subdron/data` (the `DATA_ROOT` env variable overrides it for special cases). Bags go in `/srv/subdron/data/<robot>/<mission>/03_Logs/*.bag`. |
| Tracking DB port | Default 5433. `MISSION_DB_PORT` is only needed when the DB runs on another port — the setup script detects this and writes the export to `~/.bashrc`. |
| Docker images | Versions are pinned in `local_orchestrator/images.env` (edit + commit to upgrade). Apply on any machine with `./scripts/update_local_images.sh` — it pulls the pinned versions and points the local `:dev` aliases at them. Job records store the digest of every run. |
| PHASEFORMER weights | One-time: `gcloud storage cp gs://subdron_dev/machine_learning/image_processing/weights/best.pth /srv/subdron/data/_state/models/PHASEFORMER/` (the runner prints this command if the weights are missing). |
| Failures | The trigger never retries. Fix the cause (see the log path it prints), then re-run the stage with `--force`. |

### Multi-user operation

The data root is shared and group-writable by design, so several users can work on
the same machine from their own accounts. The trigger is a **system** service: it
runs as one account regardless of who is logged in, and any user can check it
(`systemctl status subdron-pipeline-trigger.timer`) or read its logs. Onboarding a
new user is just `./scripts/setup_local_orchestrator.sh` from their own clone — the
script joins the required groups (`subdron`, `docker`, `systemd-journal`) and reuses
the machine's running DB. Code is per-user; only the data directory is shared, and
the trigger already runs machine-wide, so there is nothing to start.

---

## 6. CI/CD & Automation

The pipeline uses an automated CI/CD flow using **Bitbucket Pipelines** and **Google Cloud Build**.

### Tagging Convention (The "Safety Wall")

To ensure strict isolation between environments, the pipeline follows this tagging strategy:

| Environment | Branch | Git Tag | Image Alias |
| :--- | :--- | :--- | :--- |
| **Development** | `development` | `vX.Y.Z-dev` | `:latest` |
| **Production** | `master` | `vX.Y.Z` | `:production` |

### Automated Workflow

1.  **Merge to `development`**: Automatically tags the repo as `vX.Y.Z-dev`. Cloud Build then deploys the Dev Cloud Functions, which are configured to pull service images with the `:latest` tag.
2.  **Merge to `master`**: Automatically tags the repo as `vX.Y.Z`. Cloud Build then deploys the Production Cloud Functions, which are configured to pull service images with the `:production` tag.

### Recommended Workflow Order

1.  **Service Repos First**: Merge changes in `subdron_colmap`, `subdron_image_processing`, or `subdron_camera_transformation`. This updates the `:latest` or `:production` image alias in the registry.
2.  **Pipeline Repo Last**: Merge `subdron_pipelines` to update the Cloud Function trigger logic if necessary.

**Note**: A bug fix limited to a single service (e.g., COLMAP) only requires pushing that service repository. The existing Dev Cloud Functions already point to the `:latest` tag and automatically pull the new code on their next run.

---

## 7. Operations Guide

### Deployment Overview

While the pipeline is **fully automated via Bitbucket**, it's still possible to use manual scripts for emergency overrides or specific testing.

#### Development vs. Production

| Feature | Development (`dev`) | Production (`prod`) |
| :--- | :--- | :--- |
| **GCP Region** | `europe-west1` | `europe-west1` |
| **Bucket** | `subdron_dev` | `subdron` |
| **Image Tags** | `:latest` (automated) | `:production` (automated) |
| **Git Tags** | `v*-dev` | `v*` |
| **Isolation** | Test data in `/tests/` | Real data in `/data/` |

### Manual Script Reference

#### `deploy.sh` (Interactive Orchestrator)
The primary entry point for manual deployments. It provides interactive environment and service selection.

#### `scripts/deploy_dev.sh` / `scripts/deploy_prod.sh`
Used by Cloud Build to perform the actual `gcloud` deployment. These scripts are now "CI-aware" and will use default tags (`latest`/`production`) if run in a non-interactive environment.

---

## 8. Pipeline Roadmap

- [x] **GCP Batch Integration**: Orchestrated processing via scalable Batch jobs.
- [x] **CI/CD Automation**: Automated tagging and deployment via Bitbucket & Cloud Build.
- [x] **Safety Wall**: Environment isolation using `:latest` and `:production` tag aliases.
- [ ] **Label Studio Integration**: Triggering the pipeline directly from Label Studio for annotated data.
- [ ] **Remove Marker Dependency**: Move towards Cloud Workflows or Pub/Sub to remove dependency on marker files.
- [ ] **Real-time Monitoring Dashboard**: Centralized view of all running and historical batch jobs.
