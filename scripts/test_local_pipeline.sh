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
    echo "Usage: ./test_local_pipeline.sh --step [bag|image|colmap|full] [--build] [options]"
    exit 1
fi

# --- Optional Build ---
if [ "$BUILD_FIRST" = true ]; then
    if [ "$STEP" == "full" ]; then
        STEPS_TO_BUILD=("bag" "image")
    else
        STEPS_TO_BUILD=("$STEP")
    fi

    for B_STEP in "${STEPS_TO_BUILD[@]}"; do
        REPO_DIR="${REPO_DIRS[$B_STEP]}"
        FULL_NAME="europe-west1-docker.pkg.dev/eighth-saga-468413-c9/${IMAGE_NAMES[$B_STEP]}/${IMAGE_NAMES[$B_STEP]}:${TAG}"
        echo "Building local image: $FULL_NAME from $REPO_DIR"
        
        # Start ssh-agent and add keys for Bitbucket access
        eval "$(ssh-agent -s)"
        if [ -n "$SUDO_USER" ]; then
            USER_HOME=$(getent passwd "$SUDO_USER" | cut -d: -f6)
            KEY_PATH="$USER_HOME/.ssh/id_ed25519"
        else
            KEY_PATH="$HOME/.ssh/id_ed25519"
        fi
        ssh-add "$KEY_PATH"
        
        export DOCKER_BUILDKIT=1
        docker build --ssh default -t "$FULL_NAME" "$REPO_DIR"
        kill "${SSH_AGENT_PID}"
        
        # Cleanup dangling images to save space
        echo "Cleaning up dangling Docker images..."
        docker image prune -f
    done
fi

# Fetch the DATABASE_URL
echo "Fetching DATABASE_URL from Secret Manager..."
DB_URL=$(gcloud secrets versions access latest --secret="DB_URL" --project="eighth-saga-468413-c9")

run_bag_step() {
    local bag=$1
    local tag=$2
    local image="europe-west1-docker.pkg.dev/eighth-saga-468413-c9/subdron-rosbag-extractor/subdron-rosbag-extractor:${tag}"
    
    echo ">>> RUNNING BAG EXTRACTION: $bag"
    docker run --rm \
      --entrypoint /bin/bash \
      -v ~/.config/gcloud:/root/.config/gcloud \
      --env-file ../subdron_camera_transformation/.env.dev \
      -e DATABASE_URL="$DB_URL" \
      -e GOOGLE_APPLICATION_CREDENTIALS=/root/.config/gcloud/application_default_credentials.json \
      "$image" \
      -c "source /opt/ros/noetic/setup.bash && source /catkin_ws/devel/setup.bash && python main.py -i $bag --extract-images --post-process --upload-results --create-video --video-fps 30"
}

run_image_step() {
    local img_dir=$1
    local tag=$2
    local image="europe-west1-docker.pkg.dev/eighth-saga-468413-c9/subdron-image-processing/subdron-image-processing:${tag}"
    local out_dir="${img_dir/\/image_pose_pairs/\/enhanced\/PHASEFORMER}"

    echo ">>> RUNNING IMAGE ENHANCEMENT: $img_dir"
    docker run --rm --gpus all \
      --entrypoint /bin/bash \
      -v ~/.config/gcloud:/root/.config/gcloud \
      --env-file ../subdron_image_processing/.env.dev \
      -e DATABASE_URL="$DB_URL" \
      -e GOOGLE_APPLICATION_CREDENTIALS=/root/.config/gcloud/application_default_credentials.json \
      -e ENV=dev -e PIPELINE_ENV=dev \
      "$image" \
      -c "python -u main.py PHASEFORMER --download ${img_dir} --upload ${out_dir} --batch_size 8 --upload_every 512 --resume"
}

case $STEP in
    bag)
        if [ -z "$BAG_PATH" ]; then echo "Error: --bag-path required"; exit 1; fi
        run_bag_step "$BAG_PATH" "$TAG"
        ;;
    image)
        if [ -z "$IMAGE_DIR" ]; then echo "Error: --image-dir required"; exit 1; fi
        run_image_step "$IMAGE_DIR" "$TAG"
        ;;
    full)
        if [ -z "$BAG_PATH" ] || [ -z "$IMAGE_DIR" ]; then 
            echo "Error: Both --bag-path and --image-dir are required for the full step"; exit 1
        fi
        
        # 1. Run Bag Step
        run_bag_step "$BAG_PATH" "$TAG"

        # 2. Run Image Step
        run_image_step "$IMAGE_DIR" "$TAG"
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