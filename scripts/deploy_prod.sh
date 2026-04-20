#!/bin/bash
set -e

# --- Configuration Defaults ---
BAG_TAG=""
IMG_TAG=""
COLMAP_TAG=""
SKIP_BAG=false
SKIP_IMG=false
SKIP_COLMAP=false

# --- Parse Arguments ---
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --bag-tag)
            BAG_TAG="$2"
            shift 2
            ;;
        --img-tag)
            IMG_TAG="$2"
            shift 2
            ;;
        --colmap-tag)
            COLMAP_TAG="$2"
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
            echo "Usage: ./deploy_prod.sh [--bag-tag <tag>] [--img-tag <tag>] [--colmap-tag <tag>] [--skip bag|img|colmap|...]"
            exit 1
            ;;
    esac
done

echo " Preparing PRODUCTION Pipeline Deployment..."

# --- Gather missing tags interactively ---
if [ "$SKIP_BAG" = false ] && [ -z "$BAG_TAG" ]; then
    read -p "Enter the tag for Bag Extractor (process_bag_file) [default: production]: " INPUT_BAG_TAG
    BAG_TAG="${INPUT_BAG_TAG:-production}"
fi

if [ "$SKIP_IMG" = false ] && [ -z "$IMG_TAG" ]; then
    read -p "Enter the tag for Image Processing Service (process_images) [default: production]: " INPUT_IMG_TAG
    IMG_TAG="${INPUT_IMG_TAG:-production}"
fi

if [ "$SKIP_COLMAP" = false ] && [ -z "$COLMAP_TAG" ]; then
    read -p "Enter the tag for Colmap Service (process_colmap_prod) [default: production]: " INPUT_COLMAP_TAG
    COLMAP_TAG="${INPUT_COLMAP_TAG:-production}"
fi

echo " Starting Deployments..."

# DEPLOY BAG EXTRACTOR
if [ "$SKIP_BAG" = false ]; then
    echo "--> Deploying: Bag Extractor (process_bag_file) with tag: $BAG_TAG"
    gcloud functions deploy process_bag_file \
      --runtime python311 \
      --trigger-bucket subdron \
      --entry-point process_bag_file_prod \
      --region europe-west1 \
      --source ./rosbag_extractor/prod \
      --no-gen2 \
      --set-env-vars TARGET_IMAGE_TAG=${BAG_TAG}
else
    echo "--> SKIPPING: Bag Extractor"
fi

# Deploy Image Processing
if [ "$SKIP_IMG" = false ]; then
    echo "--> Deploying: Image Processing Service (process_images) with tag: $IMG_TAG"
    gcloud functions deploy process_images \
      --runtime python311 \
      --trigger-bucket subdron \
      --entry-point process_images_prod \
      --region europe-west1 \
      --source ./image_processing/prod \
      --no-gen2 \
      --set-env-vars TARGET_IMAGE_TAG=${IMG_TAG}
else
    echo "--> SKIPPING: Image Processing"
fi

# Deploy Colmap Service
if [ "$SKIP_COLMAP" = false ]; then
    echo "--> Deploying: Colmap Service (process_colmap_prod) with tag: $COLMAP_TAG"
    gcloud functions deploy process_colmap_prod \
      --runtime python311 \
      --trigger-bucket subdron \
      --entry-point process_colmap_prod \
      --region europe-west1 \
      --source ./colmap_processing/prod \
      --no-gen2 \
      --set-env-vars TARGET_IMAGE_TAG=${COLMAP_TAG}
else
    echo "--> SKIPPING: Colmap Service"
fi

echo " All production deployments finished!"
