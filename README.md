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

### Recommended Workflow

1.  **Local Verification**: Test your changes locally using Docker before pushing to the cloud. This ensures your code works with actual cloud data without waiting for the full event-driven trigger chain.
    ```bash
    ./scripts/test_local_pipeline.sh --step full --sample
    ```

2.  **Deployment**: Use the unified deployment script to build, push, and deploy for both dev and prod.
    ```bash
    ./deploy.sh
    ```

3.  **Verification**: Verify by uploading a file to the corresponding bucket and checking the logs in Google Cloud Console.

### Script Reference

| Script | Description |
| :--- | :--- |
| `deploy.sh` | **Primary Entry Point**. Orchestrates building, pushing, and deploying. |
| `scripts/build_and_push.sh` | Orchestrates Docker builds and pushes for all sub-repos. |
| `scripts/deploy_dev.sh` | Deploys Cloud Functions to the `dev` environment. |
| `scripts/deploy_prod.sh` | Deploys Cloud Functions to the `production` environment. |

## 7. Pipeline Roadmap

- [x] **GCP Batch Integration**: Orchestrated processing via scalable Batch jobs.
- [x] **Marker-based Triggering**: Automated sequencing using GCS completion markers.
- [x] **Environment Separation**: Distinct workflows for `dev` and `prod`.
- [ ] **Label Studio Integration**: Triggering the pipeline directly from Label Studio for annotated data.
- [ ] **Remove Marker Dependency**: Move towards a more robust orchestration (e.g., Cloud Workflows or Pub/Sub) to remove dependency on marker files in storage.
- [ ] **Real-time Monitoring Dashboard**: Centralized view of all running and historical batch jobs.
