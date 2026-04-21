# Subdron Underwater Vision Pipeline

This repository orchestrates the end-to-end processing pipeline for underwater imagery, from raw ROS bags to 3D reconstructions. It manages the deployment of Cloud Functions that trigger specialized Batch Jobs.

## Architecture

The pipeline consists of three main stages, each living in its own repository:

1.  **Bag Extractor (`subdron_camera_transformation`)**: Extracts images and navigation data from `.bag` files.
2.  **Image Processing (`subdron_image_processing`)**: Enhances image quality (e.g., using PHASEFORMER) for better feature matching.
3.  **Colmap Processing (`subdron_colmap`)**: Performs 3D sparse reconstruction using the enhanced images and navigation poses.

## Project Structure

-   `rosbag_extractor/`: Cloud Function code to trigger the Bag Extractor batch job.
-   `image_processing/`: Cloud Function code to trigger the Image Enhancement batch job.
-   `colmap_processing/`: Cloud Function code to trigger the Colmap reconstruction batch job.
-   `scripts/`: Centralized scripts for building, pushing, and deploying the entire pipeline.

## Recommended Workflow

To ensure stability, always follow this multi-stage verification process:

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
The script will walk you through:
- Selecting the environment (dev/prod).
- Deciding whether to build/push images.
- Setting the tags.

### 3. Verification
*Verify by uploading a file to the corresponding bucket and checking the logs in Google Cloud Console.*

## Script Reference

| Script | Description |
| :--- | :--- |
| `deploy.sh` | **Primary Entry Point (Root)**. Orchestrates building, pushing, and deploying. |
| `scripts/build_and_push.sh` | Orchestrates Docker builds and pushes for all sub-repos. |
| `scripts/deploy_dev.sh` | Deploys Cloud Functions to the `dev` project/bucket. |
| `scripts/deploy_prod.sh` | Deploys Cloud Functions to the `production` project/bucket. |
| `scripts/test_local_pipeline.sh` | Runs pipeline steps locally using `docker run`. |

## Prerequisites

-   Google Cloud SDK (`gcloud`) authenticated.
-   Access to the Subdron Artifact Registry.
-   Docker and Docker Compose installed.
-   SSH keys configured for private repository access during builds.
