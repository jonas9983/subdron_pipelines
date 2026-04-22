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
BASE_UPLOAD_DIR="tests" # Default to tests directory for local pipeline

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
        --base-dir) BASE_UPLOAD_DIR="$2"; shift 2 ;;
        *) echo "Unknown parameter: $1"; exit 1 ;;
    esac
done

# Normalize step
if [ "$STEP" == "img" ]; then STEP="image"; fi

if [ -z "$STEP" ]; then
    echo "Usage: ./test_local_pipeline.sh --step [bag|image|colmap|full] [--build] [options]"
    exit 1
fi

# Validate step
if [[ ! " bag image colmap full " =~ " $STEP " ]]; then
    echo "Error: Invalid step '$STEP'. Must be one of: bag, image (or img), colmap, full"
    exit 1
fi

# --- Optional Build ---
if [ "$BUILD_FIRST" = true ]; then
    if [ "$STEP" == "full" ]; then
        STEPS_TO_BUILD=("bag" "image" "colmap")
    else
        STEPS_TO_BUILD=("$STEP")
    fi

    for B_STEP in "${STEPS_TO_BUILD[@]}"; do
        REPO_DIR="${REPO_DIRS[$B_STEP]}"
        FULL_NAME="europe-west1-docker.pkg.dev/eighth-saga-468413-c9/${IMAGE_NAMES[$B_STEP]}/${IMAGE_NAMES[$B_STEP]}:${TAG}"
        
        # Detect Dockerfile location
        DOCKERFILE_PATH="$REPO_DIR/Dockerfile"
        if [ ! -f "$DOCKERFILE_PATH" ]; then
            DOCKERFILE_PATH="$REPO_DIR/docker/Dockerfile"
        fi
        
        if [ ! -f "$DOCKERFILE_PATH" ]; then
            echo "Error: Could not find Dockerfile in $REPO_DIR or $REPO_DIR/docker/"
            exit 1
        fi

        echo "Building local image: $FULL_NAME from $REPO_DIR (using $DOCKERFILE_PATH)"
        
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
        docker build --ssh default -f "$DOCKERFILE_PATH" -t "$FULL_NAME" "$REPO_DIR"
        kill "${SSH_AGENT_PID}"
        
        # Cleanup dangling images to save space
        echo "Cleaning up dangling Docker images..."
        docker image prune -f
    done
fi

# Fetch the DATABASE_URL
echo "Fetching DATABASE_URL from Secret Manager..."
DB_URL=$(gcloud secrets versions access latest --secret="DB_URL" --project="eighth-saga-468413-c9")

# Dynamically detect bucket and base_dir from paths
if [[ "$BAG_PATH" == gs://* ]]; then
    BUCKET_NAME=$(echo "$BAG_PATH" | cut -d/ -f3)
elif [[ "$BAG_PATH" == */* ]]; then
    BUCKET_NAME=$(echo "$BAG_PATH" | cut -d/ -f1)
else
    BUCKET_NAME="subdron-raw-data-dev"
fi

# Infer BASE_UPLOAD_DIR (e.g., 'tests' or 'data') from image_dir if provided
if [ -n "$IMAGE_DIR" ]; then
    # Extract the part after the bucket: subdron_dev/tests/... -> tests
    BASE_UPLOAD_DIR=$(echo "$IMAGE_DIR" | sed "s|^$BUCKET_NAME/||" | cut -d/ -f1)
fi

echo "Using Bucket: $BUCKET_NAME"
echo "Using Base Dir: $BASE_UPLOAD_DIR"

run_bag_step() {
    local bag=$1
    local tag=$2
    local image="europe-west1-docker.pkg.dev/eighth-saga-468413-c9/subdron-rosbag-extractor/subdron-rosbag-extractor:${tag}"
    
    # Strip gs:// to handle literal copy-paste from GCS UI
    local bag_clean=${bag#gs://}

    echo " RUNNING BAG EXTRACTION: $bag_clean"
    docker run --rm \
      --entrypoint /bin/bash \
      -v ~/.config/gcloud:/root/.config/gcloud \
      -v "$HOME/git/subdron-utils/subdron_utils:/usr/local/lib/python3.8/dist-packages/subdron_utils" \
      --env-file ../subdron_camera_transformation/.env.dev \
      -e DATABASE_URL="$DB_URL" \
      -e BASE_UPLOAD_DIR="$BASE_UPLOAD_DIR" \
      -e CLOUD_BUCKET_NAME="$BUCKET_NAME" \
      -e GOOGLE_APPLICATION_CREDENTIALS=/root/.config/gcloud/application_default_credentials.json \
      "$image" \
      -c "source /opt/ros/noetic/setup.bash && source /catkin_ws/devel/setup.bash && python main.py -i $bag_clean --extract-images --post-process --upload-results --create-video --video-fps 30"
}

run_image_step() {
    local img_dir=$1
    local tag=$2
    local image="europe-west1-docker.pkg.dev/eighth-saga-468413-c9/subdron-image-processing/subdron-image-processing:${tag}"
    
    # Strip gs://
    local img_dir_clean=${img_dir#gs://}
    local out_dir="${img_dir_clean/\/image_pose_pairs/\/enhanced\/PHASEFORMER}"

    echo " RUNNING IMAGE ENHANCEMENT: $img_dir_clean"
    docker run --rm --gpus all \
      --entrypoint /bin/bash \
      -v ~/.config/gcloud:/root/.config/gcloud \
      -v "$HOME/git/subdron-utils/subdron_utils:/opt/venv/lib/python3.9/site-packages/subdron_utils" \
      --env-file ../subdron_image_processing/.env.dev \
      -e DATABASE_URL="$DB_URL" \
      -e GOOGLE_APPLICATION_CREDENTIALS=/root/.config/gcloud/application_default_credentials.json \
      -e ENV=dev -e PIPELINE_ENV=dev \
      -e BASE_UPLOAD_DIR="$BASE_UPLOAD_DIR" \
      -e CLOUD_BUCKET_NAME="$BUCKET_NAME" \
      "$image" \
      -c "python -u main.py PHASEFORMER --download ${img_dir_clean} --upload ${out_dir} --batch_size 8 --upload_every 512 --resume --create-video --video-fps 30"
}

run_colmap_step() {
    local img_dir=$1
    local poses_file=$2
    local tag=$3
    local image="europe-west1-docker.pkg.dev/eighth-saga-468413-c9/subdron-colmap/subdron-colmap:${tag}"

    # Strip gs:// from all inputs
    local img_dir_clean=${img_dir#gs://}
    local poses_file_clean=${poses_file#gs://}

    local enhanced_dir=$COLMAP_IMAGES_PREFIX
    if [ -z "$enhanced_dir" ]; then
        enhanced_dir="${img_dir_clean/\/image_pose_pairs/\/enhanced\/PHASEFORMER}"
    fi
    enhanced_dir=${enhanced_dir#gs://}

    echo " RUNNING COLMAP RECONSTRUCTION: $enhanced_dir"
    docker run --rm --gpus all \
      --entrypoint /bin/bash \
      -v ~/.config/gcloud:/root/.config/gcloud \
      -v "$HOME/git/subdron-utils/subdron_utils:/opt/venv/lib/python3.10/site-packages/subdron_utils" \
      --env-file ../subdron_colmap/.env.dev \
      -e DATABASE_URL="$DB_URL" \
      -e GOOGLE_APPLICATION_CREDENTIALS=/root/.config/gcloud/application_default_credentials.json \
      -e ENV=dev \
      -e BASE_UPLOAD_DIR="$BASE_UPLOAD_DIR" \
      -e CLOUD_BUCKET_NAME="$BUCKET_NAME" \
      "$image" \
      -c "python -u main.py --images-prefix ${enhanced_dir} --poses-prefix ${poses_file_clean} --dir-version PHASEFORMER_spatial --matching-strategy spatial --spatial-radius 5.0 --plane-mode single --feature-extractor sift --upload-results"
}

case $STEP in
    bag)
        if [ -z "$BAG_PATH" ]; then
            echo "Error: --bag-path required"
            exit 1
        fi
        run_bag_step "$BAG_PATH" "$TAG"
        ;;
    image)
        if [ -z "$IMAGE_DIR" ]; then
            echo "Error: --image-dir required"
            exit 1
        fi
        run_image_step "$IMAGE_DIR" "$TAG"
        ;;
    colmap)
        if [ -z "$IMAGE_DIR" ] && [ -z "$COLMAP_IMAGES_PREFIX" ]; then
            echo "Error: --image-dir or --colmap-images-prefix is required for colmap step"
            exit 1
        fi
        if [ -z "$COLMAP_POSES_PREFIX" ]; then 
            echo "Error: --colmap-poses-prefix (csv file) is required for colmap step"
            exit 1
        fi
        run_colmap_step "$IMAGE_DIR" "$COLMAP_POSES_PREFIX" "$TAG"
        ;;
    full)
        if [ -z "$BAG_PATH" ] || [ -z "$IMAGE_DIR" ]; then 
            echo "Error: Both --bag-path and --image-dir are required for the full step"
            exit 1
        fi

        # 1. Run Bag Step
        run_bag_step "$BAG_PATH" "$TAG"

        # 2. Run Image Step
        run_image_step "$IMAGE_DIR" "$TAG"

        # 3. Run Colmap Step
        if [ -n "$COLMAP_POSES_PREFIX" ]; then
            run_colmap_step "$IMAGE_DIR" "$COLMAP_POSES_PREFIX" "$TAG"
        else
            echo "Skipping Colmap step in 'full' pipeline because --colmap-poses-prefix was not provided."
        fi
        ;;
    *)
        echo "Invalid step: $STEP"
        exit 1
        ;;
esac

echo "Step $STEP completed successfully!"
