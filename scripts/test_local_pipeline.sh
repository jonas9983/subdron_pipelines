#!/bin/bash
set -e

# --- Configuration ---
TAG="local-test"
STEP=""
BAG_PATH=""
IMAGE_DIR=""
COLMAP_IMAGES_PREFIX=""
COLMAP_POSES_PREFIX=""
BUILD_FIRST=false

# Map steps to their local repository directories
declare -A REPO_DIRS
REPO_DIRS=(
    ["bag"]="../subdron_camera_transformation"
    ["image"]="../subdron_image_processing"
    ["colmap"]="../subdron_colmap"
)

# Map steps to their registry image names
declare -A IMAGE_NAMES
IMAGE_NAMES=(
    ["bag"]="subdron-rosbag-extractor"
    ["image"]="subdron-image-processing"
    ["colmap"]="subdron-colmap"
)

# --- Parse Arguments ---
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --step) STEP="$2"; shift 2 ;;
        --tag) TAG="$2"; shift 2 ;;
        -i|--input|--bag-path) BAG_PATH="$2"; shift 2 ;;
        -d|--image-dir) IMAGE_DIR="$2"; shift 2 ;;
        --colmap-images-prefix) COLMAP_IMAGES_PREFIX="$2"; shift 2 ;;
        --colmap-poses-prefix) COLMAP_POSES_PREFIX="$2"; shift 2 ;;
        --build) BUILD_FIRST=true; shift 1 ;;
        *) echo "Unknown parameter: $1"; exit 1 ;;
    esac
done

if [ -z "$STEP" ]; then
    echo "Usage: ./test_local_pipeline.sh --step [bag|image|colmap] [--build] [options]"
    exit 1
fi

FULL_IMAGE_NAME="europe-west1-docker.pkg.dev/eighth-saga-468413-c9/${IMAGE_NAMES[$STEP]}/${IMAGE_NAMES[$STEP]}:${TAG}"

# --- Optional Build ---
if [ "$BUILD_FIRST" = true ]; then
    REPO_DIR="${REPO_DIRS[$STEP]}"
    echo "Building local image: $FULL_IMAGE_NAME from $REPO_DIR"
    
    # Start ssh-agent and add keys for Bitbucket access
    eval "$(ssh-agent -s)"

    # Determine the correct SSH key path (handle sudo)
    if [ -n "$SUDO_USER" ]; then
        USER_HOME=$(getent passwd "$SUDO_USER" | cut -d: -f6)
        KEY_PATH="$USER_HOME/.ssh/id_ed25519"
    else
        KEY_PATH="$HOME/.ssh/id_ed25519"
    fi

    if [ -f "$KEY_PATH" ]; then
        echo "Adding SSH key: $KEY_PATH"
        ssh-add "$KEY_PATH"
    else
        echo "Warning: SSH key not found at $KEY_PATH. Build might fail if it needs Bitbucket access."
    fi

    # Use BuildKit and SSH forwarding for Bitbucket access
    export DOCKER_BUILDKIT=1
    docker build --ssh default -t "$FULL_IMAGE_NAME" "$REPO_DIR"

    
    # Stop ssh-agent
    kill "${SSH_AGENT_PID}"
fi

echo "Running local test for step: $STEP using tag: $TAG"

# Fetch the DATABASE_URL from Secret Manager to mimic Cloud Batch secret injection
echo "Fetching DATABASE_URL from Secret Manager..."
DB_URL=$(gcloud secrets versions access latest --secret="DB_URL" --project="eighth-saga-468413-c9")

case $STEP in
    bag)
        if [ -z "$BAG_PATH" ]; then echo "Error: --bag-path required for bag step"; exit 1; fi
        
        CMD="source /opt/ros/noetic/setup.bash && source /catkin_ws/devel/setup.bash && python -u main.py -i $BAG_PATH --extract-images --post-process --upload-results --create-video --video-fps 30"
        
        echo "Executing command inside container: $CMD"
        
        docker run --rm \
          --entrypoint /bin/bash \
          -v ~/.config/gcloud:/root/.config/gcloud \
          --env-file ../subdron_camera_transformation/.env.dev \
          -e DATABASE_URL="$DB_URL" \
          -e GOOGLE_APPLICATION_CREDENTIALS=/root/.config/gcloud/application_default_credentials.json \
          "$FULL_IMAGE_NAME" \
          -c "$CMD"
        ;;
    image)
        if [ -z "$IMAGE_DIR" ]; then echo "Error: --image-dir required for image step"; exit 1; fi
        OUTPUT_DIR="${IMAGE_DIR/\/image_pose_pairs/\/enhanced\/PHASEFORMER}"
        
        docker run --rm --gpus all \
          --entrypoint /bin/bash \
          -v ~/.config/gcloud:/root/.config/gcloud \
          --env-file ../subdron_image_processing/.env.dev \
          -e DATABASE_URL="$DB_URL" \
          -e GOOGLE_APPLICATION_CREDENTIALS=/root/.config/gcloud/application_default_credentials.json \
          -e ENV=dev -e PIPELINE_ENV=dev \
          "$FULL_IMAGE_NAME" \
          -c "python -u main.py PHASEFORMER --download ${IMAGE_DIR} --upload ${OUTPUT_DIR} --batch_size 8 --upload_every 512 --resume"
        ;;
    colmap)
        if [ -z "$COLMAP_IMAGES_PREFIX" ] || [ -z "$COLMAP_POSES_PREFIX" ]; then 
            echo "Error: --colmap-images-prefix and --colmap-poses-prefix required for colmap step"; exit 1
        fi
        docker run --rm --gpus all \
          --entrypoint /bin/bash \
          -v ~/.config/gcloud:/root/.config/gcloud \
          --env-file ../subdron_colmap/.env.dev \
          -e DATABASE_URL="$DB_URL" \
          -e GOOGLE_APPLICATION_CREDENTIALS=/root/.config/gcloud/application_default_credentials.json \
          -e ENV=dev \
          "$FULL_IMAGE_NAME" \
          -c "python -u main.py --images-prefix ${COLMAP_IMAGES_PREFIX} --poses-prefix ${COLMAP_POSES_PREFIX} --dir-version PHASEFORMER_spatial --matching-strategy spatial --spatial-radius 5.0 --plane-mode single --feature-extractor sift --upload-results"
        ;;
    *)
        echo "Invalid step: $STEP"
        exit 1
        ;;
esac

echo "Step $STEP completed successfully!"
