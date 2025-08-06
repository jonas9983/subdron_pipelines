#!/bin/bash

# Exit immediately if a command exits with a non-zero status.
set -e

# --- Argument Parsing ---
CONFIG_FILE=""

# Loop through all command-line arguments
while [ "$#" -gt 0 ]; do
    case "$1" in
        --config)
            CONFIG_FILE="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Check if a config file is provided
if [ -z "$CONFIG_FILE" ]; then
    echo "Error: The --config argument is required."
    exit 1
fi

# Read parameters from the JSON file using jq
SOURCE=$(jq -r '.source' "$CONFIG_FILE")
IMAGE_PREFIX=$(jq -r '.image_prefix' "$CONFIG_FILE")
POSES_PREFIX=$(jq -r '.poses_prefix' "$CONFIG_FILE")
METHOD=$(jq -r '.method' "$CONFIG_FILE")

# --- Dynamic Path Generation ---
# Assuming image-processing saves to a directory named after the method.
# This path is relative to the shared volume mount point inside the container.
IMAGE_DIR="/colmap/projects/$METHOD"

# --- Pipeline Execution ---
echo "Starting image-processing service..."
# Run the image-processing service with the parsed arguments
docker compose run --rm image-processing --to_memory --prefix "$IMAGE_PREFIX" "$METHOD"

echo "Image processing completed. Starting COLMAP service..."
# Run the colmap service, passing the dynamic path as an environment variable.
docker compose run --rm -e COLMAP_SOURCE_PATH="$IMAGE_DIR" -e POSES_PREFIX="$POSES_PREFIX" colmap --source "$SOURCE"

echo "COLMAP workflow completed."