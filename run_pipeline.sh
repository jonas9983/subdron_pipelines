#!/bin/bash

# Exit immediately if a command exits with a non-zero status.
set -e

# --- Argument Parsing ---
# Initialize variables with default values
SOURCE=""
PREFIX=""
METHOD=""

# Loop through all command-line arguments
while [ "$#" -gt 0 ]; do
    case "$1" in
        --source)
            SOURCE="$2"
            shift 2
            ;;
        --prefix)
            PREFIX="$2"
            shift 2
            ;;
        --method)
            METHOD="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Check if required arguments are provided
if [ -z "$SOURCE" ] || [ -z "$PREFIX" ] || [ -z "$METHOD" ]; then
    echo "Error: The --source, --prefix, and --method arguments are required."
    exit 1
fi

# --- Dynamic Path Generation ---
# Assuming image-processing saves to a directory named after the method.
# This path is relative to the shared volume mount point inside the container.
IMAGE_DIR="/colmap/projects/$METHOD"

# --- Pipeline Execution ---
echo "Starting image-processing service..."
# Run the image-processing service with the parsed arguments
docker compose run --rm image-processing --to_memory --prefix "$PREFIX" "$METHOD"

echo "Image processing completed. Starting COLMAP service..."
# Run the colmap service, passing the dynamic path as an environment variable.
docker compose run --rm -e COLMAP_SOURCE_PATH="$IMAGE_DIR" colmap --source "$SOURCE"

echo "COLMAP workflow completed."