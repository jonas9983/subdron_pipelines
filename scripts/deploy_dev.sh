#!/bin/bash
set -e

# --- Configuration Defaults ---
BAG_TAG=""
IMG_TAG=""
COLMAP_TAG=""
TRIGGER_BUCKET=""
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
            echo "Usage: ./deploy_dev.sh [--bag-tag <tag>] [--img-tag <tag>] [--colmap-tag <tag>] [--bucket <bucket>] [--skip bag|img|colmap|...]"
            exit 1
            ;;
    esac
done

echo " Preparing DEV Pipeline Deployment..."

# --- Gather missing configuration only if not provided ---
# --- Gather missing configuration only if not provided ---
if [ -z "$TRIGGER_BUCKET" ]; then
    if [ -t 0 ]; then
        read -p "Enter the Trigger Bucket name [default: subdron_dev]: " INPUT_BUCKET
        TRIGGER_BUCKET="${INPUT_BUCKET:-subdron_dev}"
    else
        TRIGGER_BUCKET="subdron_dev"
        echo "Non-interactive shell detected. Using default bucket: $TRIGGER_BUCKET"
    fi
fi

echo " Trigger Bucket: $TRIGGER_BUCKET"

if [ "$SKIP_BAG" = false ] && [ -z "$BAG_TAG" ]; then
    if [ -t 0 ]; then
        read -p "Enter the tag for Bag Extractor (process_bag_file_dev): " BAG_TAG
        if [ -z "$BAG_TAG" ]; then
            echo "ERROR: Bag tag cannot be empty."
            exit 1
        fi
    else
        BAG_TAG="latest"
        echo "Non-interactive shell detected. Using default Bag tag: $BAG_TAG"
    fi
fi

if [ "$SKIP_IMG" = false ] && [ -z "$IMG_TAG" ]; then
    if [ -t 0 ]; then
        read -p "Enter the tag for Image Processing Service (process_images_dev): " IMG_TAG
        if [ -z "$IMG_TAG" ]; then
            echo "ERROR: Image processing tag cannot be empty."
            exit 1
        fi
    else
        IMG_TAG="latest"
        echo "Non-interactive shell detected. Using default Image tag: $IMG_TAG"
    fi
fi

if [ "$SKIP_COLMAP" = false ] && [ -z "$COLMAP_TAG" ]; then
    if [ -t 0 ]; then
        read -p "Enter the tag for Colmap Service (process_colmap_dev): " COLMAP_TAG
        if [ -z "$COLMAP_TAG" ]; then
            echo "ERROR: Colmap tag cannot be empty."
            exit 1
        fi
    else
        COLMAP_TAG="latest"
        echo "Non-interactive shell detected. Using default Colmap tag: $COLMAP_TAG"
    fi
fi

echo " Starting Deployments..."

# DEPLOY BAG EXTRACTOR
if [ "$SKIP_BAG" = false ]; then
    echo "--> Deploying: Bag Extractor (process_bag_file_dev) with tag: $BAG_TAG"
    gcloud functions deploy process_bag_file_dev \
      --runtime python311 \
      --trigger-bucket $TRIGGER_BUCKET \
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
      --trigger-bucket $TRIGGER_BUCKET \
      --entry-point process_images_dev \
      --region europe-west1 \
      --source ./image_processing/dev \
      --no-gen2 \
      --set-env-vars TARGET_IMAGE_TAG=${IMG_TAG}
else
    echo "--> SKIPPING: Image Processing"
fi

# Deploy Colmap Service
if [ "$SKIP_COLMAP" = false ]; then
    echo "--> Deploying: Colmap Service (process_colmap_dev) with tag: $COLMAP_TAG"
    gcloud functions deploy process_colmap_dev \
      --runtime python311 \
      --trigger-bucket $TRIGGER_BUCKET \
      --entry-point process_colmap_dev \
      --region europe-west1 \
      --source ./colmap_processing/dev \
      --no-gen2 \
      --set-env-vars TARGET_IMAGE_TAG=${COLMAP_TAG}
else
    echo "--> SKIPPING: Colmap Service"
fi

echo " All deployments finished!"
