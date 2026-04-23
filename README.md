# Subdron Underwater Vision Pipeline

This repository orchestrates the end-to-end processing pipeline for underwater imagery, from raw ROS bags to 3D reconstructions. It manages the deployment of Cloud Functions that trigger specialized Google Cloud Batch Jobs.

## Workflow Diagram


## Architecture

The pipeline is event-driven and uses marker files to sequence processing stages across Google Cloud Batch:

1.  **Rosbag Extractor**: Triggered by `.bag` file uploads to GCS. Extracts images and navigation data.
2.  **Image Processing**: Triggered by the `EXTRACTION_COMPLETE.marker`. Enhances image quality (PHASEFORMER/CLAHE).
3.  **Colmap Processing**: Triggered by the `ENHANCEMENT_COMPLETE.marker`. Performs 3D reconstruction and spatial matching.

## Key Features

- **Automated Sequencing**: Uses GCS Object Finalize events and JSON marker files to pass metadata between stages.
- **Scalable Processing**: Leverages GCP Batch for high-throughput processing with automatic resource provisioning.
- **GPU Support**: Integrated NVIDIA T4 GPU support for deep-learning-based enhancement (PHASEFORMER).
- **Security**: The GCP secret `DB_URL` is injected as the `DATABASE_URL` environment variable into processing containers.
- **Environment Isolation**: Distinct `dev` and `prod` configurations for safe testing and stable production runs.
- **Dynamic Tagging**: Control container versions via `TARGET_IMAGE_TAG` environment variable in Cloud Functions.

## Project Structure

- `rosbag_extractor/`: Cloud Function code to trigger the `subdron_camera_transformation` Extractor batch job.
- `image_processing/`: Cloud Function code to trigger the `subdron_image_processing` batch job.
- `colmap_processing/`: Cloud Function code to trigger the `subdron_colmap` reconstruction batch job.
- `scripts/`: Centralized scripts for building, pushing, and deploying the entire pipeline.

## Environment Workflow

### Development (`dev`)
- **Bucket Path**: `tests/`
- **Container Tags**: Defaults to `dev`. Can be overridden with `TARGET_IMAGE_TAG`.
- **Purpose**: Rapid testing of new algorithms and matching strategies.

### Production (`prod`)
- **Bucket Path**: `data/`
- **Container Tags**: Defaults to `production`.
- **Purpose**: Stable, finalized pipeline for mission data.

## Recommended Workflow

### 1. Local Verification
Test your changes locally using Docker before pushing to the cloud.
```bash
./scripts/test_local_pipeline.sh --step full --build --bag-path /path/to/test.bag --image-dir /path/to/output
```

### 2. Deployment
Use the unified deployment script to build, push, and deploy in one go.
```bash
./deploy.sh
```

### 3. Verification
Verify by uploading a file to the corresponding bucket and checking the logs in Google Cloud Console.

## Script Reference

| Script | Description |
| :--- | :--- |
| `deploy.sh` | **Primary Entry Point**. Orchestrates building, pushing, and deploying. |
| `scripts/build_and_push.sh` | Orchestrates Docker builds and pushes for all sub-repos. |
| `scripts/deploy_dev.sh` | Deploys Cloud Functions to the `dev` environment. |
| `scripts/deploy_prod.sh` | Deploys Cloud Functions to the `production` environment. |

## Prerequisites

- Google Cloud SDK (`gcloud`) authenticated and configured.
- Access to the Subdron Artifact Registry.
- Docker and Docker Compose installed.
- Secret Manager access for `DB_URL`.
