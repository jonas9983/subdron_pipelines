#!/bin/bash
set -e

# --- Configuration ---
FUNCTION_NAME="process_bag_file_dev"
REGION="europe-west1"
BUCKET_NAME="subdron" 
ENTRY_POINT="process_bag_file_dev"
SOURCE_DIR="./rosbag_extractor/dev" 

# --- 1. Get Tag from Argument ---
if [ -z "$1" ]; then
    echo "Usage: ./deploy_dev.sh <target_image_tag>"
    echo "Example: ./deploy_dev.sh v2.0-test"
    exit 1
fi

TAG=$1

echo "--- Deploying Cloud Function: ${FUNCTION_NAME} ---"
echo "--- Source Directory: ${SOURCE_DIR} ---"
echo "--- Target Image Tag for Batch Jobs: ${TAG} ---"

gcloud functions deploy ${FUNCTION_NAME} \
  --runtime python310 \
  --trigger-bucket ${BUCKET_NAME} \
  --entry-point ${ENTRY_POINT} \
  --region ${REGION} \
  --source ${SOURCE_DIR} \
  --no-gen2 \
  --set-env-vars TARGET_IMAGE_TAG=${TAG}

echo "--- Deployment Complete ---"