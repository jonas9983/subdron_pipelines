#!/bin/bash
set -e

# --- Configuration Defaults ---
BAG_TAG=""
IMG_TAG=""
SKIP_BAG=false
SKIP_IMG=false

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
        --skip)
            # Check if the skip argument contains "bag" or "img"
            if [[ "$2" == *"bag"* ]]; then SKIP_BAG=true; fi
            if [[ "$2" == *"img"* ]]; then SKIP_IMG=true; fi
            shift 2
            ;;
        *)
            echo "Unknown parameter: $1"
            echo "Usage: ./deploy_dev.sh [--bag-tag <tag>] [--img-tag <tag>] [--skip bag|img|bag,img]"
            exit 1
            ;;
    esac
done

echo " Preparing DEV Pipeline Deployment..."

# --- Gather missing tags interactively ---
if [ "$SKIP_BAG" = false ] && [ -z "$BAG_TAG" ]; then
    read -p "Enter the tag for Bag Extractor (process_bag_file_dev): " BAG_TAG
    if [ -z "$BAG_TAG" ]; then
        echo "ERROR: Bag tag cannot be empty. Use --skip bag if you want to skip it."
        exit 1
    fi
fi

if [ "$SKIP_IMG" = false ] && [ -z "$IMG_TAG" ]; then
    read -p "Enter the tag for Image Processing Service (process_images_dev): " IMG_TAG
    if [ -z "$IMG_TAG" ]; then
        echo "ERROR: Image processing tag cannot be empty. Use --skip img if you want to skip it."
        exit 1
    fi
fi

echo " Starting Deployments..."

# DEPLOY BAG EXTRACTOR
if [ "$SKIP_BAG" = false ]; then
    echo "--> Deploying: Bag Extractor (process_bag_file_dev) with tag: $BAG_TAG"
    gcloud functions deploy process_bag_file_dev \
      --runtime python311 \
      --trigger-bucket subdron \
      --entry-point process_bag_file_dev \
      --region europe-west1 \
      --source ./rosbag_extractor/dev \
      --no-gen2 \
      --set-env-vars TARGET_IMAGE_TAG=${BAG_TAG}
else
    echo "--> SKIPPING: Bag Extractor"
fi

# Deploy Image Processing
if [ "$SKIP_IMG" = false ]; then
    echo "--> Deploying: Image Processing Service (process_images_dev) with tag: $IMG_TAG"
    gcloud functions deploy process_images_dev \
      --runtime python311 \
      --trigger-bucket subdron \
      --entry-point process_images_dev \
      --region europe-west1 \
      --source ./image_processing/dev \
      --no-gen2 \
      --set-env-vars TARGET_IMAGE_TAG=${IMG_TAG}
else
    echo "--> SKIPPING: Image Processing"
fi

echo " All deployments finished!"
