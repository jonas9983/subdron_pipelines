#!/bin/bash
set -e

# --- Configuration Defaults ---
TAG=""
SKIP_BAG=false
SKIP_IMG=false

# --- Parse Arguments ---
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --tag)
            TAG="$2"
            shift 2
            ;;
        --skip)
            # Check if the skip argument contains "bag" or "img"
            if [[ "$2" == *"bag"* ]]; then SKIP_BAG=true; fi
            if [[ "$2" == *"img"* ]]; then SKIP_IMG=true; fi
            shift 2
            ;;
        *)
            echo "Unknown parameter: $1"
            echo "Usage: ./deploy_dev.sh --tag <tag> [--skip bag|img|bag,img]"
            exit 1
            ;;
    esac
done

if [ -z "$TAG" ]; then
    echo "ERROR: You must provide a target image tag."
    echo "Example: ./deploy_dev.sh --tag v2.0-test"
    exit 1
fi

echo " Starting DEV Pipeline Deployment (Target Tag: $TAG)"

# DEPLOY BAG EXTRACTOR
if [ "$SKIP_BAG" = false ]; then
    echo "--> Deploying: Bag Extractor (process_bag_file_dev)"
    gcloud functions deploy process_bag_file_dev \
      --runtime python311 \
      --trigger-bucket subdron \
      --entry-point process_bag_file_dev \
      --region europe-west1 \
      --source ./rosbag_extractor/dev \
      --no-gen2 \
      --set-env-vars TARGET_IMAGE_TAG=${TAG}
else
    echo "--> SKIPPING: Bag Extractor"
fi

# Deploy Image Processing
if [ "$SKIP_IMG" = false ]; then
    echo " Deploying: Image Processing Service (process_images_dev)"
    gcloud functions deploy process_images_dev \
      --runtime python311 \
      --trigger-bucket subdron \
      --entry-point process_images_dev \
      --region europe-west1 \
      --source ./image_processing/dev \
      --no-gen2 \
      --set-env-vars TARGET_IMAGE_TAG=${TAG}
else
    echo "SKIPPING: Image Processing"
fi

echo " All deployments finished!"
