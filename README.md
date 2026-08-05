# Subdron Underwater Vision Pipeline

This repository orchestrates the end-to-end processing pipeline for underwater imagery, from raw ROS bags to 3D reconstructions. It manages the deployment of Cloud Functions that trigger specialized Google Cloud Batch Jobs.


## 1. Prerequisites

Before setting up the pipeline, ensure you meet the following requirements:

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

## 5. Local Testing & Verification

The `scripts/test_local_pipeline.sh` script is a critical tool for developers. It allows you to run specific stages (or the full pipeline) locally using Docker containers, but still interacting with cloud data.

### Why use it?

*   **Ignore Markers**: Unlike the Cloud Functions, which wait for `.marker` files, this script allows you to trigger a job on any data by providing the path directly.
*   **Immediate Feedback**: See container logs in your terminal instantly.
*   **Algorithm Tuning**: Test new container versions (via `--tag`) on real cloud data without deploying Cloud Functions.

### Usage & Arguments

```bash
./scripts/test_local_pipeline.sh --step <step> [arguments]
```

| Argument | Description |
| :--- | :--- |
| `--step` | The pipeline stage to run: `bag`, `image`, `colmap`, or `full`. |
| `-i`, `--input` | GCS Path to the ROS bag (e.g., `gs://bucket/tests/robot/mission/.../file.bag`). |
| `-d`, `--image-dir` | GCS path where images are/will be stored. |
| `--tag` | The Docker tag to use for the container (default: `local-test`). |
| `--build` | Build the Docker image locally from source before running. |
| `--colmap-poses-prefix` | **Required for colmap step**. GCS path to the navigation CSV file. |
| `--sample` | Runs a predefined full pipeline test on sample data.<br>• `mission_id`: `P1063`<br>• `timestamp`: `2026-03-10-10-54-33` |

### Example: Test Image Enhancement Only

```bash
./scripts/test_local_pipeline.sh --step image -d gs://my-bucket/tests/ROB01/MISS01/camera/TS/image_pose_pairs
```

---

## 6. Local Orchestrator (offline processing)

For machines with no Google Cloud access, `local_orchestrator/` runs pipeline stages
fully offline against a local data tree and a local Postgres for mission tracking
(same `log_mission_stage` pathway the cloud services use).

One-time setup (pyenv virtualenv `pipelines` — or `.venv` fallback — with subdron-utils
pinned to a release tag, plus the tracking DB):

```bash
./scripts/setup_local_orchestrator.sh
```

### Stage runners (`local_orchestrator/runners/`)

One script per stage, one bag per invocation (paths relative to `DATA_ROOT`, default `<repo>/data`):

```bash
python local_orchestrator/runners/run_extraction.py  data/sparus2-25/P1063/03_Logs/<bag>.bag
python local_orchestrator/runners/run_phaseformer.py data/sparus2-25/P1063/02_Post_Mission/camera/<ts>/image_pose_pairs
python local_orchestrator/runners/run_colmap.py      data/sparus2-25/P1063/02_Post_Mission/camera/<ts>/enhanced/PHASEFORMER
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
python local_orchestrator/trigger/approve_bag_for_processing.py data/.../<bag>.bag --ranges 0:500 1400:2100
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

### Environment & machine prerequisites

| What | How |
| :--- | :--- |
| Data root | `DATA_ROOT` env (default `<repo>/data`). Bags go in `data/<source>/<mission>/03_Logs/*.bag`. |
| Tracking DB port | `MISSION_DB_PORT` (default 5433). Export both in `~/.bashrc`; the systemd unit carries its own copies. |
| Docker images | Pull from Artifact Registry and retag locally, e.g. `docker pull europe-west1-docker.pkg.dev/.../subdron-colmap:1.4.0 && docker tag ... subdron-colmap:dev`. Prefer version tags over `latest`; job records store the digest of every run. |
| PHASEFORMER weights | One-time: `gcloud storage cp gs://subdron_dev/machine_learning/image_processing/weights/best.pth $DATA_ROOT/_state/models/PHASEFORMER/` (the runner prints this command if they are missing). |
| Failures | The trigger never retries. Fix the cause (see the log path it prints), then re-run the stage with `--force`. |

### Multi-user operation (shared machine)

The trigger is a **system** service: it runs as one account regardless of who is
logged in, and any user can check it (`systemctl status subdron-pipeline-trigger.timer`).
To let several users review/approve and read logs:

```bash
# journal access for another user
sudo usermod -aG systemd-journal <user>

# shared data root with group-writable state (one-time)
sudo groupadd subdron
sudo usermod -aG subdron <each user>
sudo mkdir -p /srv/subdron/data
sudo chown -R <service-user>:subdron /srv/subdron
sudo chmod -R 2775 /srv/subdron          # setgid: new files inherit the group
setfacl -R -d -m g::rwx /srv/subdron     # new files stay group-writable
```

Then every user (and the systemd unit) sets `DATA_ROOT=/srv/subdron/data`. Code is
per-user: each account clones the repo and runs `setup_local_orchestrator.sh` for its
own pyenv env; only the data/state tree is shared. Users who run stages manually also
need the `docker` group.

---

## 7. CI/CD & Automation

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

**Note**: If you only fix a bug in a service (e.g., COLMAP), you only need to push that service repository. The existing Dev Cloud Functions already point to the `:latest` tag and will automatically pull your new code on their next run.

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
The primary entry point for manual deployments. It guides you through environment selection and service selection.

#### `scripts/deploy_dev.sh` / `scripts/deploy_prod.sh`
Used by Cloud Build to perform the actual `gcloud` deployment. These scripts are now "CI-aware" and will use default tags (`latest`/`production`) if run in a non-interactive environment.

---
## 8. Local Offline Pipeline
Local pipeline without connection to the Cloud or database. 
 

### 8.1. Prerequisites & Data Setup
The `data/` directory must sit in the same parent directory as the repositories. 

The pipeline automatically creates output directories, provided the input path contains the **robot name**, **mission ID**, and **ROS bag file**. 

It is recommended that the data folder structure is similar to the Google Cloud Storage format, so the robot name, mission id and timestamp can be extracted and the output paths can be built smoothly. 
An example: 

```text
parent_folder/
├── data/
│   └── {robot_name}/
│       └── {mission_id}/
│           └── 03_Logs/
│               └── {bag_name}.bag
├── subdron_camera_transformation/
├── subdron_image_processing/
└── subdron_colmap/
...
``` 
### 8.2. Basic Usage
To run the complete pipeline from start to end:

```
./scripts/run_offline_pipeline.sh --tag TAG --step full -i BAG_PATH  
```

Example: 

```
./scripts/run_offline_pipeline.sh --tag latest --step full -i data/sparus2-25/P1063/03_Logs/P1063_2026-03-10-10-54-33.bag
``` 

### 8.3. Arguments
The arguments are the similar to the ones described in the section **5. Local Testing & Verification** of the README, **except** for the `--sample` and `--base-dir` arguments that were removed.

This script also **tracks performance metrics**, which can be saved by using the `--save-metrics` flag.

### 8.4. Outputs
Depending on the execution, the script generates:

* **Pipeline Data:** The processed data folders (extracted frames, enhanced images, and COLMAP 3D reconstructions) saved directly inside the target mission structure within `data/`.
* **Execution Metrics (`offline_pipeline_metrics.csv`):** Generated only when `--save-metrics` is enabled. It records a CSV entry with the following fields:
  * `timestamp`: Date and time of execution
  * `bag`: Name of the input ROS bag file
  * `bag_step`: Duration of ROS bag extraction (seconds)
  * `image_step`: Duration of image enhancement (seconds)
  * `colmap_step`: Duration of 3D reconstruction (seconds)
  * `total`: Total pipeline execution time (seconds)
  * `image_count`: Total number of processed images
  * `bag_size_mb`: File size of the input ROS bag (MB)

---

## 9. Pipeline Roadmap

- [x] **GCP Batch Integration**: Orchestrated processing via scalable Batch jobs.
- [x] **CI/CD Automation**: Automated tagging and deployment via Bitbucket & Cloud Build.
- [x] **Safety Wall**: Environment isolation using `:latest` and `:production` tag aliases.
- [ ] **Label Studio Integration**: Triggering the pipeline directly from Label Studio for annotated data.
- [ ] **Remove Marker Dependency**: Move towards Cloud Workflows or Pub/Sub to remove dependency on marker files.
- [ ] **Real-time Monitoring Dashboard**: Centralized view of all running and historical batch jobs.
