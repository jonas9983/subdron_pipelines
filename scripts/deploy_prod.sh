#!/bin/bash
set -e

# --- Configuration Defaults ---
RELEASE_TAG=""
TRIGGER_BUCKET=""
SKIP_BAG=false
SKIP_IMG=false
SKIP_COLMAP=false

# --- Parse Arguments ---
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --tag|--release-tag)
            RELEASE_TAG="$2"
            shift 2
            ;;
        --bucket)
            TRIGGER_BUCKET="$2"
            shift 2
            ;;
        --skip)
            # Check if the skip argument contains "bag", "img", or "colmap"
            if [[ "$2" == *"bag"* ]]; then SKIP_BAG=true; fi
            if [[ "$2" == *"img"* ]]; then SKIP_IMG=true; fi
            if [[ "$2" == *"colmap"* ]]; then SKIP_COLMAP=true; fi
            shift 2
            ;;
        *)
            echo "Unknown parameter: $1"
            echo "Usage: ./deploy_prod.sh [--tag <release_tag>] [--bucket <bucket>] [--skip bag|img|colmap|...]"
            exit 1
            ;;
    esac
done

echo " Preparing PRODUCTION Pipeline Deployment..."

# --- Gather missing configuration only if not provided ---
# --- Gather missing configuration only if not provided ---
if [ -z "$TRIGGER_BUCKET" ]; then
    if [ -t 0 ]; then
        read -p "Enter the PRODUCTION Trigger Bucket name [default: subdron]: " INPUT_BUCKET
        TRIGGER_BUCKET="${INPUT_BUCKET:-subdron}"
    else
        TRIGGER_BUCKET="subdron"
        echo "Non-interactive shell detected. Using default bucket: $TRIGGER_BUCKET"
    fi
fi

if [ -z "$RELEASE_TAG" ]; then
    if [ -t 0 ]; then
        read -p "Enter the RELEASE TAG for all services [default: production]: " INPUT_TAG
        RELEASE_TAG="${INPUT_TAG:-production}"
    else
        RELEASE_TAG="production"
        echo "Non-interactive shell detected. Using default release tag: $RELEASE_TAG"
    fi
fi

echo " Trigger Bucket: $TRIGGER_BUCKET"
echo " Release Tag:    $RELEASE_TAG"

echo " Starting Deployments..."

# DEPLOY BAG EXTRACTOR
if [ "$SKIP_BAG" = false ]; then
    echo "--> Deploying: Bag Extractor (process_bag_file) with tag: $RELEASE_TAG"
    gcloud functions deploy process_bag_file \
      --runtime python311 \
      --trigger-bucket $TRIGGER_BUCKET \
      --entry-point process_bag_file_prod \
      --region europe-west1 \
      --source ./rosbag_extractor/prod \
      --no-gen2 \
      --set-env-vars TARGET_IMAGE_TAG=${RELEASE_TAG},BASE_UPLOAD_DIR=data
else
    echo "--> SKIPPING: Bag Extractor"
fi

# Deploy Image Processing
if [ "$SKIP_IMG" = false ]; then
    echo "--> Deploying: Image Processing Service (process_images) with tag: $RELEASE_TAG"
    gcloud functions deploy process_images \
      --runtime python311 \
      --trigger-bucket $TRIGGER_BUCKET \
      --entry-point process_images_prod \
      --region europe-west1 \
      --source ./image_processing/prod \
      --no-gen2 \
      --set-env-vars TARGET_IMAGE_TAG=${RELEASE_TAG},BASE_UPLOAD_DIR=data
else
    echo "--> SKIPPING: Image Processing"
fi

# Deploy Colmap Service
if [ "$SKIP_COLMAP" = false ]; then
    echo "--> Deploying: Colmap Service (process_colmap_prod) with tag: $RELEASE_TAG"
    gcloud functions deploy process_colmap_prod \
      --runtime python311 \
      --trigger-bucket $TRIGGER_BUCKET \
      --entry-point process_colmap_prod \
      --region europe-west1 \
      --source ./colmap_processing/prod \
      --no-gen2 \
      --set-env-vars TARGET_IMAGE_TAG=${RELEASE_TAG},BASE_UPLOAD_DIR=data
else
    echo "--> SKIPPING: Colmap Service"
fi

echo " All production deployments finished!"
