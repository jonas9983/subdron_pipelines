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

# Detect the base git directory (parent of this repo)
# Allows overriding via GIT_ROOT environment variable
DEFAULT_GIT_ROOT=$(cd "$(dirname "$0")/../.." && pwd)
GIT_ROOT="${GIT_ROOT:-$DEFAULT_GIT_ROOT}"

echo " Using GIT_ROOT: $GIT_ROOT"

# Map steps to their local repository directories
declare -A REPO_DIRS
REPO_DIRS=(
    ["bag"]="$GIT_ROOT/subdron_camera_transformation"
    ["image"]="$GIT_ROOT/subdron_image_processing"
    ["colmap"]="$GIT_ROOT/subdron_colmap"
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
        --sample) 
            BAG_PATH="subdron_dev/tests/sparus2-25/P1063/03_Logs/test_bags/P1063_2026-03-10-10-54-33.bag"
            STEP="full"
            shift 1
            ;;
        *) echo "Unknown parameter: $1"; exit 1 ;;
    esac
done

# Normalize step
if [ "$STEP" == "img" ]; then STEP="image"; fi

if [ -z "$STEP" ] && [ -z "$BAG_PATH" ]; then
    echo "Usage: ./test_local_pipeline.sh --step [bag|image|colmap|full] [-i bag_path] [--sample]"
    exit 1
fi

# Dynamically detect bucket and environment from bag path
if [ -n "$BAG_PATH" ]; then
    # Strip gs:// for logic
    BAG_CLEAN=${BAG_PATH#gs://}
    BUCKET_NAME=$(echo "$BAG_CLEAN" | cut -d/ -f1)
    BASE_UPLOAD_DIR=$(echo "$BAG_CLEAN" | cut -d/ -f2)
    ROBOT=$(echo "$BAG_CLEAN" | cut -d/ -f3)
    MISSION_ID=$(echo "$BAG_CLEAN" | cut -d/ -f4)
    
    # Extract timestamp from filename: P1063_2026-03-10-10-54-33.bag -> 2026-03-10-10-54-33
    FILENAME=$(basename "$BAG_CLEAN")
    TIMESTAMP=$(echo "$FILENAME" | grep -oE "[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]{2}-[0-9]{2}-[0-9]{2}")

    echo "--- Derived Mission Info ---"
    echo " Bucket:    $BUCKET_NAME"
    echo " Base Dir:  $BASE_UPLOAD_DIR"
    echo " Robot:     $ROBOT"
    echo " Mission:   $MISSION_ID"
    echo " Timestamp: $TIMESTAMP"
    echo "----------------------------"

    # Auto-construct missing paths
    if [ -z "$IMAGE_DIR" ]; then
        IMAGE_DIR="$BUCKET_NAME/$BASE_UPLOAD_DIR/$ROBOT/$MISSION_ID/02_Post_Mission/camera/$TIMESTAMP/image_pose_pairs"
    fi
    if [ -z "$COLMAP_POSES_PREFIX" ]; then
        COLMAP_POSES_PREFIX="$BUCKET_NAME/$BASE_UPLOAD_DIR/$ROBOT/$MISSION_ID/02_Post_Mission/navigation/$TIMESTAMP/navigation_not_converted.csv"
    fi
fi

# Fetch the DATABASE_URL if not already set
if [ -z "$DATABASE_URL" ]; then
    echo "Fetching DATABASE_URL from Secret Manager..."
    DB_URL=$(gcloud secrets versions access latest --secret="DB_URL" --project="eighth-saga-468413-c9")
else
    echo "Using existing DATABASE_URL from environment."
    DB_URL="$DATABASE_URL"
fi

# --- 1. Build if requested ---
if [ "$BUILD_FIRST" = true ]; then
    if [ -n "$STEP" ] && [ "$STEP" != "full" ]; then
        REPO_DIR="${REPO_DIRS[$STEP]}"
        echo "--- Building image locally: $STEP in $REPO_DIR ---"
        # Determine correct build script path
        BUILD_SCRIPT="./shell_scripts/build.sh"
        if [ "$STEP" == "image" ] || [ "$STEP" == "colmap" ]; then BUILD_SCRIPT="./shell_scripts/build.dev.sh"; fi
        
        (cd "$REPO_DIR" && bash "$BUILD_SCRIPT" --tag "$TAG")
    elif [ "$STEP" == "full" ]; then
        for S in "bag" "image" "colmap"; do
            REPO_DIR="${REPO_DIRS[$S]}"
            echo "--- Building image locally: $S in $REPO_DIR ---"
            BUILD_SCRIPT="./shell_scripts/build.sh"
            if [ "$S" == "image" ] || [ "$S" == "colmap" ]; then BUILD_SCRIPT="./shell_scripts/build.dev.sh"; fi
            (cd "$REPO_DIR" && bash "$BUILD_SCRIPT" --tag "$TAG")
        done
    fi
fi

run_bag_step() {
    local bag=$1
    local tag=$2
    local image="subdron-rosbag-extractor:${tag}"
    
    # Strip gs:// to handle literal copy-paste from GCS UI
    local bag_clean=${bag#gs://}

    echo " RUNNING BAG EXTRACTION (Local Code): $bag_clean"
    docker run --rm \
      --entrypoint /bin/bash \
      -v ~/.config/gcloud:/root/.config/gcloud \
      -v "$GIT_ROOT/subdron-utils/subdron_utils:/usr/local/lib/python3.8/dist-packages/subdron_utils" \
      -v "$GIT_ROOT/subdron_camera_transformation:/app" \
      -w /app \
      --env-file ../subdron_camera_transformation/.env.dev \
      -e DATABASE_URL="$DB_URL" \
      -e BASE_UPLOAD_DIR="$BASE_UPLOAD_DIR" \
      -e CLOUD_BUCKET_NAME="$BUCKET_NAME" \
      -e GOOGLE_APPLICATION_CREDENTIALS=/root/.config/gcloud/application_default_credentials.json \
      "$image" \
      -c "source /opt/ros/noetic/setup.bash && source /catkin_ws/devel/setup.bash && python3 main.py -i $bag_clean --extract-images --post-process --upload-results --create-video --video-fps 30"
}

run_image_step() {
    local img_dir=$1
    local tag=$2
    local image="subdron-image-processing:${tag}"
    
    # Strip gs://
    local img_dir_clean=${img_dir#gs://}
    local out_dir="${img_dir_clean/\/image_pose_pairs/\/enhanced\/PHASEFORMER}"

    echo " RUNNING IMAGE ENHANCEMENT (Local Code): $img_dir_clean"
    docker run --rm --gpus all \
      --entrypoint /bin/bash \
      -v ~/.config/gcloud:/root/.config/gcloud \
      -v "$GIT_ROOT/subdron-utils/subdron_utils:/opt/venv/lib/python3.9/site-packages/subdron_utils" \
      -v "$GIT_ROOT/subdron_image_processing:/app" \
      -w /app \
      --env-file ../subdron_image_processing/.env.dev \
      -e DATABASE_URL="$DB_URL" \
      -e GOOGLE_APPLICATION_CREDENTIALS=/root/.config/gcloud/application_default_credentials.json \
      -e ENV=dev -e PIPELINE_ENV=dev \
      -e BASE_UPLOAD_DIR="$BASE_UPLOAD_DIR" \
      -e CLOUD_BUCKET_NAME="$BUCKET_NAME" \
      "$image" \
      -c "python3 -u main.py PHASEFORMER --download ${img_dir_clean} --upload ${out_dir} --batch_size 8 --upload_every 512 --resume --create-video --video-fps 30"
}

run_colmap_step() {
    local img_dir=$1
    local poses_file=$2
    local tag=$3
    local image="subdron-colmap:${tag}"

    # Strip gs:// from all inputs
    local img_dir_clean=${img_dir#gs://}
    local poses_file_clean=${poses_file#gs://}

    local enhanced_dir=$COLMAP_IMAGES_PREFIX
    if [ -z "$enhanced_dir" ]; then
        enhanced_dir="${img_dir_clean/\/image_pose_pairs/\/enhanced\/PHASEFORMER}"
    fi
    enhanced_dir=${enhanced_dir#gs://}

    echo " RUNNING COLMAP RECONSTRUCTION (Local Code): $enhanced_dir"
    docker run --rm --gpus all \
      --entrypoint /bin/bash \
      -v ~/.config/gcloud:/root/.config/gcloud \
      -v "$GIT_ROOT/subdron-utils/subdron_utils:/opt/venv/lib/python3.10/site-packages/subdron_utils" \
      -v "$GIT_ROOT/subdron_colmap:/app" \
      -w /app \
      --env-file ../subdron_colmap/.env.dev \
      -e DATABASE_URL="$DB_URL" \
      -e GOOGLE_APPLICATION_CREDENTIALS=/root/.config/gcloud/application_default_credentials.json \
      -e ENV=dev \
      -e BASE_UPLOAD_DIR="$BASE_UPLOAD_DIR" \
      -e CLOUD_BUCKET_NAME="$BUCKET_NAME" \
      "$image" \
      -c "python3 -u main.py --images-prefix ${enhanced_dir} --poses-prefix ${poses_file_clean} --dir-version PHASEFORMER_spatial --matching-strategy spatial --spatial-radius 5.0 --plane-mode single --feature-extractor sift --upload-results"
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
