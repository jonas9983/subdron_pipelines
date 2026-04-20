#!/bin/bash
set -e

# --- Configuration ---
# Map repository folder names to their specific push script paths
declare -A REPO_SCRIPTS
REPO_SCRIPTS=(
    ["subdron_camera_transformation"]="../subdron_camera_transformation/scripts/push_to_registry.sh"
    ["subdron_image_processing"]="../subdron_image_processing/shell_scripts/push_to_registry.sh"
    ["subdron_colmap"]="../subdron_colmap/shell_scripts/push_to_registry.sh"
)

TAG=""

# --- Parse Arguments ---
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --tag)
            TAG="$2"
            shift 2
            ;;
        *)
            echo "Unknown parameter: $1"
            echo "Usage: ./build_and_push.sh --tag <tag_name>"
            exit 1
            ;;
    esac
done

if [ -z "$TAG" ]; then
    read -p "Enter the TAG for the images (e.g., v1.0.0 or dev): " TAG
    if [ -z "$TAG" ]; then echo "Error: Tag is required."; exit 1; fi
fi

echo "Starting centralized Build and Push process for tag: $TAG"

for REPO in "${!REPO_SCRIPTS[@]}"; do
    SCRIPT_PATH="${REPO_SCRIPTS[$REPO]}"
    
    echo "--------------------------------------------------------"
    echo "Processing Repository: $REPO"
    
    if [ ! -f "$SCRIPT_PATH" ]; then
        echo "Error: Script not found at $SCRIPT_PATH. Skipping..."
        continue
    fi

    # Determine paths
    REPO_ROOT="../$REPO"
    SCRIPT_DIR="$(dirname "$SCRIPT_PATH")"
    SCRIPT_NAME="$(basename "$SCRIPT_PATH")"
    
    # Handle .env.dev file
    ENV_FILE="$REPO_ROOT/.env.dev"
    TARGET_ENV="$SCRIPT_DIR/.env.dev"
    CLEANUP_ENV=false

    if [ -f "$ENV_FILE" ]; then
        if [ ! -f "$TARGET_ENV" ]; then
            echo "Copying .env.dev to $SCRIPT_DIR"
            cp "$ENV_FILE" "$TARGET_ENV"
            CLEANUP_ENV=true
        fi
    else
        echo "Warning: No .env.dev found in $REPO_ROOT"
    fi

    echo "Executing: $SCRIPT_PATH --tag $TAG"
    
    # Run the script. (cd ...) ensures we are in the right place
    (cd "$SCRIPT_DIR" && ./"$SCRIPT_NAME" --tag "$TAG")

    # Cleanup temporary env file if we created it
    if [ "$CLEANUP_ENV" = true ]; then
        echo "Cleaning up temporary .env.dev in $SCRIPT_DIR"
        rm "$TARGET_ENV"
    fi

    echo "Finished processing: $REPO"
done

echo "--------------------------------------------------------"
echo "All repository push scripts have been executed!"
