#!/bin/bash

# Exit immediately if a command exits with a non-zero status.
set -e

# --- Argument Parsing ---
# Initialize variables with default values
SOURCE="holoocean"
PREFIX=""
METHOD=""

# Loop through all command-line arguments
while [ "$#" -gt 0 ]; do
    case "$1" in
        --source)
            SOURCE="$2"
            shift 2 # Skip the argument name and its value
            ;;
        --prefix)
            PREFIX="$2"
            shift 2 # Skip the argument name and its value
            ;;
        --method)
            METHOD="$2"
            shift 2 # Skip the argument name and its value
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Check if required arguments are provided
if [ -z "$PREFIX" ]; then
    echo "Error: The --prefix argument is required."
    exit 1
fi

if [ -z "$METHOD" ]; then
    echo "Error: The --method argument is required."
    exit 1
fi

# --- Pipeline Execution ---
echo "Starting image-processing service..."
# Run the image-processing service with the parsed arguments
docker compose run --rm image-processing --download --prefix "$PREFIX" "$METHOD"

echo "Image processing completed. Starting COLMAP service..."
# Run the colmap service with the parsed source argument
docker compose run --rm colmap --source "$SOURCE"

echo "COLMAP workflow completed."