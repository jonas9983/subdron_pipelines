# Image processing pipeline without connection to Google Cloud and database.

#!/bin/bash
set -e

# --- Configuration ---
TAG="latest"
STEP=""
BAG_PATH=""
IMAGE_DIR=""
ENHANCED_DIR=""
POSES_PATH=""
BUILD_FIRST=false  
BASE_UPLOAD_DIR="tests" # Default to tests directory for local pipeline


CSV_FILE="pipeline_timings.csv"
BAG_TIME=""
IMAGE_TIME=""
COLMAP_TIME=""
TOTAL_TIME=""
IMAGE_COUNT=""

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
        --enhanced-dir) ENHANCED_DIR="$2"; shift 2 ;;
        --poses-path) POSES_PATH="$2"; shift 2 ;;
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
    echo "Usage: ./test_offline_pipeline.sh --step [bag|image|colmap|full] [-i bag_path] [--sample]"
    exit 1
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
    #local bag_clean=${bag#gs://}

    echo " RUNNING BAG EXTRACTION (Local Code): $bag"
    docker run --rm \
      --user $(id -u):$(id -g) \
      --entrypoint /bin/bash \
      -v ~/.config/gcloud:/root/.config/gcloud \
      -v "$GIT_ROOT/subdron-utils/subdron_utils:/usr/local/lib/python3.8/dist-packages/subdron_utils" \
      -v "$GIT_ROOT/subdron_camera_transformation:/app" \
      -v "$GIT_ROOT/data:/app/data" \
      -w /app \
      --env-file ../subdron_camera_transformation/.env.dev \
      -e DATABASE_URL="$DB_URL" \
      -e BASE_UPLOAD_DIR="$BASE_UPLOAD_DIR" \
      -e CLOUD_BUCKET_NAME="$BUCKET_NAME" \
      -e GOOGLE_APPLICATION_CREDENTIALS=/root/.config/gcloud/application_default_credentials.json \
      "$image" \
      -c "source /opt/ros/noetic/setup.bash && source /catkin_ws/devel/setup.bash && python3 main.py -i $bag --extract-images --post-process --create-video --video-fps 30" # Remove upload results
}

run_image_step() {
    local img_dir=$1
    local tag=$2
    local image="subdron-image-processing:${tag}"
    
    echo " RUNNING IMAGE ENHANCEMENT (Local Code): $img_dir"
    #docker run --rm --gpus all \
    docker run --rm --runtime=nvidia \
      --user $(id -u):$(id -g) \
      --entrypoint /bin/bash \
      -v "$GIT_ROOT/subdron-utils/subdron_utils:/opt/venv/lib/python3.9/site-packages/subdron_utils" \
      -v "$GIT_ROOT/subdron_image_processing:/app" \
      -v "$GIT_ROOT/data:/app/data" \
      -w /app \
      --env-file ../subdron_image_processing/.env.dev \
      -e OFFLINE_MODE=true \
      -e DISABLE_DB=true \
      -e ENV=dev -e PIPELINE_ENV=dev \
      -e BASE_UPLOAD_DIR="$BASE_UPLOAD_DIR" \
      "$image" \
      -c "python3 -u main.py PHASEFORMER --input_dir ${img_dir} --batch_size 8 --resume --create-video --video-fps 30 --keep_input"
}

run_colmap_step() {
    local enhanced_dir=$1
    local poses_file=$2
    local tag=$3
    local image="subdron-colmap:${tag}"

    # Strip gs:// from all inputs
    #local img_dir_clean=${img_dir#gs://}
    #local poses_file_clean=${poses_file#gs://}

    #local enhanced_dir=$ENHANCED_DIR
    #if [ -z "$enhanced_dir" ]; then
    #    enhanced_dir="${img_dir_clean/\/image_pose_pairs/\/enhanced\/PHASEFORMER}"
    #fi
    #enhanced_dir=${enhanced_dir#gs://}

    echo " RUNNING COLMAP RECONSTRUCTION (Local Code): $enhanced_dir"
    #docker run --rm --gpus all \
    docker run --rm --runtime=nvidia \
      --user $(id -u):$(id -g) \
      --entrypoint /bin/bash \
      -v ~/.config/gcloud:/root/.config/gcloud \
      -v "$GIT_ROOT/subdron-utils/subdron_utils:/opt/venv/lib/python3.10/site-packages/subdron_utils" \
      -v "$GIT_ROOT/subdron_colmap:/app" \
      -v "$GIT_ROOT/data:/app/data" \
      -w /app \
      --env-file ../subdron_colmap/.env.dev \
      -e GOOGLE_APPLICATION_CREDENTIALS=/root/.config/gcloud/application_default_credentials.json \
      -e ENV=dev \
      -e BASE_UPLOAD_DIR="$BASE_UPLOAD_DIR" \
      -e CLOUD_BUCKET_NAME="$BUCKET_NAME" \
      "$image" \
      -c "python3 -u main.py --images-prefix ${enhanced_dir} --poses-prefix ${poses_file} --dir-version PHASEFORMER_spatial --matching-strategy spatial --spatial-radius 5.0 --plane-mode single --feature-extractor sift"
}

parse_bag_metadata_docker() {
    local target_path=$1
    local tag=${TAG:-latest}
    #local image="subdron-rosbag-extractor:${tag}"
    local image="subdron-rosbag-extractor:dev" # CHnage this after


    IFS='|' read -r SOURCE MISSION_ID TIMESTAMP < <(docker run --rm \
      --entrypoint /bin/bash \
      -v "$GIT_ROOT/data:/app/data" \
      "$image" \
      -c "python3 -W ignore -c \"
import sys, os, contextlib
import subdron_utils

path = '$target_path'

# Redirect stdout to devnull so library prints don't contaminate our stream
with open(os.devnull, 'w') as fnull:
    with contextlib.redirect_stdout(fnull):
        mission_id, timestamp, _ = subdron_utils.get_mission_info(path)
        source = subdron_utils.infer_source_from_path(path)

# Print ONLY our clean string to stdout
print(f'{source}|{mission_id}|{timestamp}')
\"")

    echo " Auto-extracted Metadata (via Docker):"
    echo "  - Source:     $SOURCE"
    echo "  - Mission ID: $MISSION_ID"
    echo "  - Timestamp:  $TIMESTAMP"
}

compute_paths() {
    local bag_path=$1

    # 1. Extract metadata (this sets $SOURCE, $MISSION_ID, and $TIMESTAMP)
    parse_bag_metadata_docker "$bag_path"

    # 2. Compute output paths
    # Note: Added '02_Post_Mission' to IMAGE_DIR to keep it consistent with your tree structure
    IMAGE_DIR="data/$SOURCE/$MISSION_ID/02_Post_Mission/camera/$TIMESTAMP/image_pose_pairs"
    ENHANCED_DIR="data/$SOURCE/$MISSION_ID/02_Post_Mission/camera/$TIMESTAMP/enhanced/PHASEFORMER"
    POSES_PATH="data/$SOURCE/$MISSION_ID/02_Post_Mission/navigation/$TIMESTAMP/navigation_not_converted.csv"
    COLMAP_OUTPUT_DIR="data/$SOURCE/$MISSION_ID/02_Post_Mission/results/colmap/$TIMESTAMP/PHASEFORMER_spatial"

    echo ""
    echo " Computed Pipeline Paths:"
    echo "  - IMAGE_DIR:         $IMAGE_DIR"
    echo "  - ENHANCED_DIR:      $ENHANCED_DIR"
    echo "  - POSES_PATH:        $POSES_PATH"
    echo "  - COLMAP_OUTPUT_DIR: $COLMAP_OUTPUT_DIR"
}

move_folder() {
    local src_dir="$1"
    local dest_dir="$2"

    # 1. Validate that arguments are provided
    if [ -z "$src_dir" ] || [ -z "$dest_dir" ]; then
        echo " Error: Both source and destination paths are required." >&2
        echo "Usage: move_folder <source_path> <destination_path>" >&2
        return 1
    fi

    # 2. Check if source folder exists
    if [ ! -d "$src_dir" ]; then
        echo " Error: Source directory does not exist: $src_dir" >&2
        return 1
    fi

    echo " Moving directory..." >&2
    echo "  - From: $src_dir" >&2
    echo "  - To:   $dest_dir" >&2

    # 3. Get destination parent directory and create it if it doesn't exist
    local dest_parent
    dest_parent=$(dirname "$dest_dir")
    mkdir -p "$dest_parent"

    # 4. If destination directory already exists, clear it out first to avoid nested/corrupted moves
    if [ -d "$dest_dir" ]; then
        rm -rf "$dest_dir"
    fi

    # 5. Move the folder
    mv "$src_dir" "$dest_dir"

    echo " Successfully moved folder to: $dest_dir" >&2
}

count_images() {
    local img_dir=$1

    find "$img_dir" -type f \( \
        -iname "*.jpg" -o \
        -iname "*.jpeg" -o \
        -iname "*.png" \
    \) | wc -l
}

PIPELINE_START=$(date +%s)

case $STEP in
    bag)
        if [ -z "$BAG_PATH" ]; then
            echo "Error: --bag-path required"
            exit 1
        fi
        START=$(date +%s)
        run_bag_step "$BAG_PATH" "$TAG"
        END=$(date +%s)
        BAG_TIME=$((END - START))
        echo "ROS Bag extraction step took ${BAG_TIME} seconds."
        ;;
    image)
        # If IMAGE_DIR is not provided, try to detect it automatically from BAG_PATH
        if [ -z "$IMAGE_DIR" ] && [ -n "$BAG_PATH" ]; then
            compute_image_dir_path "$BAG_PATH" 
        fi

        if [ -z "$IMAGE_DIR" ]; then
            echo "Error: --image-dir required"
            exit 1
        fi

        IMAGE_COUNT=$(count_images "$IMAGE_DIR")
        START=$(date +%s)

        # Run the processing step
        run_image_step "$IMAGE_DIR" "$TAG"
        
        END=$(date +%s)
        IMAGE_TIME=$((END - START))
        echo "Image enhacing took ${IMAGE_TIME} seconds."
        ;;
    colmap)
        if [ -z "$ENHANCED_DIR" ]; then
            echo "Error: --image-dir or --enhanced-dir is required for colmap step"
            exit 1
        fi
        if [ -z "$POSES_PATH" ]; then 
            echo "Error: --poses-path (csv file) is required for colmap step"
            exit 1
        fi
        START=$(date +%s)
        run_colmap_step "$ENHANCED_DIR" "$POSES_PATH" "$TAG"
        END=$(date +%s)
        COLMAP_TIME=$((END - START))
        echo "Colmap took ${COLMAP_TIME} seconds."
        ;;
    full)
        if [ -z "$BAG_PATH" ]; then 
            echo "Error:--bag-path is required for the full step"
            exit 1
        fi

        # 1. Compute paths from bag_path
        compute_paths "$BAG_PATH"

        # 2. Run Bag Step
        START=$(date +%s)
        run_bag_step "$BAG_PATH" "$TAG"
        END=$(date +%s)
        BAG_TIME=$((END - START))
        echo "ROS Bag extraction step took ${BAG_TIME} seconds."

        # 3. Run Image Step
        if [ -z "$IMAGE_DIR" ]; then
            echo "Error: --image-dir required"
            exit 1
        fi

        IMAGE_COUNT=$(count_images "$GIT_ROOT/$IMAGE_DIR")

        START=$(date +%s)     
        run_image_step "$IMAGE_DIR" "$TAG"
        END=$(date +%s)
        IMAGE_TIME=$((END - START))
        echo "Image enhacing took ${IMAGE_TIME} seconds."

        ## Move outputs
        move_folder "$GIT_ROOT/subdron_image_processing/images/PHASEFORMER" "$GIT_ROOT/$ENHANCED_DIR"        

        # 4. Run Colmap Step
        # Auto-detect POSES_PATH if not provided
        if [ -n "$POSES_PATH" ]; then
            START=$(date +%s)
            run_colmap_step "$ENHANCED_DIR" "$POSES_PATH" "$TAG"
            END=$(date +%s)
            COLMAP_TIME=$((END - START))
            echo "Colmap took ${COLMAP_TIME} seconds."
            move_folder "$GIT_ROOT/subdron_colmap/projects/$SOURCE/results/PHASEFORMER_spatial" "$GIT_ROOT/$COLMAP_OUTPUT_DIR"

        else
            echo "Skipping Colmap step in 'full' pipeline because --poses-path could not be found."
        fi
        ;;
    *)
        echo "Invalid step: $STEP"
        exit 1
        ;;
esac

echo "Step $STEP completed successfully!"

PIPELINE_END=$(date +%s)
TOTAL_TIME=$((PIPELINE_END - PIPELINE_START))

echo "Total pipeline time: ${TOTAL_TIME} seconds."

if [ ! -f "$CSV_FILE" ]; then
    echo "timestamp,bag,bag_step,image_step,colmap_step,total, image_count" > "$CSV_FILE"
fi

echo "$(date '+%Y-%m-%d %H:%M:%S'),$(basename "$BAG_PATH"),$BAG_TIME,$IMAGE_TIME,$COLMAP_TIME,$TOTAL_TIME, $IMAGE_COUNT"  >> "$CSV_FILE"


