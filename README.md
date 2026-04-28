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

## 6. Operations Guide

### Deployment Overview

The pipeline supports two primary environments: **Development** and **Production**. Deployment is orchestrated through a unified `deploy.sh` script that manages the lifecycle of both Docker images and Cloud Functions.

#### Development vs. Production

| Feature | Development (`dev`) | Production (`prod`) |
| :--- | :--- | :--- |
| **GCP Region** | `europe-west1` | `europe-west1` |
| **Bucket** | `subdron_dev` | `subdron_dev` (using `/data` prefix) |
| **Tags** | Flexible (e.g., `dev`, `feature-x`, `test-123`) | Strict (e.g., `production` or semantic version `v1.2.0`) |
| **Isolation** | Test data in `/tests/` prefix | Real data in `/data/` prefix |
| **Purpose** | Testing new code, algorithms, and configurations. | Stable, verified mission processing. |

### Recommended Workflow

1.  **Local Verification**: Always test your changes locally using `scripts/test_local_pipeline.sh` before deploying to the cloud.
2.  **Deployment**: Run the main deployment orchestrator:
    ```bash
    ./deploy.sh
    ```
    The script will interactively guide you through:
    - **Environment Selection**: Choose between `dev` and `prod`.
    - **Service Selection**: Choose to deploy `all` services or specific ones (`bag`, `img`, `colmap`).
    - **Build/Push**: Decide whether to rebuild and push Docker images to Artifact Registry.
    - **Tagging**: Specify the Docker tags to use (individual tags for dev, or a single release tag for prod).

3.  **Verification**: After deployment, verify by:
    - Checking the Cloud Functions console for successful deployment.
    - Uploading a sample file to GCS and monitoring the logs in **Cloud Logging**.

### Script Reference

#### `deploy.sh` (Interactive Orchestrator)
This is the **primary entry point** for all deployments. It wraps the lower-level scripts to ensure consistency.

#### `scripts/build_and_push.sh`
Used for Docker image management.
- `--step <bag|image|colmap>`: (Optional) Build only a specific service.
- `--tag <tag>`: (Optional) Specify the Docker tag (default: `latest` or `dev`).

#### `scripts/deploy_dev.sh`
Directly deploys Cloud Functions to the dev environment.
- `--bag-tag <tag>`: Tag for the Bag Extractor container.
- `--img-tag <tag>`: Tag for the Image Processing container.
- `--colmap-tag <tag>`: Tag for the Colmap Processing container.
- `--bucket <name>`: Override the trigger bucket (default: `subdron_dev`).
- `--skip <bag|img|colmap>`: Skip specific services during deployment.

#### `scripts/deploy_prod.sh`
Directly deploys Cloud Functions to the production environment.
- `--tag <tag>`: Mandatory. A single tag used for all production services (e.g., `production`).
- `--skip <bag|img|colmap>`: Skip specific services during deployment.

## 7. Pipeline Roadmap

- [x] **GCP Batch Integration**: Orchestrated processing via scalable Batch jobs.
- [x] **Marker-based Triggering**: Automated sequencing using GCS completion markers.
- [x] **Environment Separation**: Distinct workflows for `dev` and `prod`.
- [ ] **Label Studio Integration**: Triggering the pipeline directly from Label Studio for annotated data.
- [ ] **Remove Marker Dependency**: Move towards a more robust orchestration (e.g., Cloud Workflows or Pub/Sub) to remove dependency on marker files in storage.
- [ ] **Real-time Monitoring Dashboard**: Centralized view of all running and historical batch jobs.
