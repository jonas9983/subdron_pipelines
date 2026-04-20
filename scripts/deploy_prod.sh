#!/bin/bash
set -e

# --- Configuration Defaults ---
TAG="production"

# --- Parse Arguments ---
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --tag)
            TAG="$2"
            shift 2
            ;;
        *)
            echo "Unknown parameter: $1"
            echo "Usage: ./deploy_prod.sh [--tag <tag>]"
            exit 1
            ;;
    esac
done

echo " Starting PRODUCTION Pipeline Deployment (Target Tag: $TAG)"

# DEPLOY BAG EXTRACTOR
echo "--> Overwriting: Bag Extractor (process_bag_file)"
gcloud functions deploy process_bag_file \
  --runtime python311 \
  --trigger-bucket subdron \
  --entry-point process_bag_file_prod \
  --region europe-west1 \
  --source ./rosbag_extractor/prod \
  --no-gen2 \
  --set-env-vars TARGET_IMAGE_TAG=${TAG}

# Deploy Image Processing
echo "--> Overwriting: Image Processing Service (process_images)"
gcloud functions deploy process_images \
  --runtime python311 \
  --trigger-bucket subdron \
  --entry-point process_images_prod \
  --region europe-west1 \
  --source ./image_processing/prod \
  --no-gen2 \
  --set-env-vars TARGET_IMAGE_TAG=${TAG}

echo " All production deployments finished!"
