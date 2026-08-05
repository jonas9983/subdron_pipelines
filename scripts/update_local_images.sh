#!/bin/bash
# Pull the image versions pinned in local_orchestrator/images.env and point the
# local :dev aliases (what the runners use) at them. Run after editing the pins.
# DRY_RUN=1 ./scripts/update_local_images.sh   -> print what would happen.
set -e

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
source "$REPO_ROOT/local_orchestrator/images.env"

declare -A VERSIONS=(
    ["subdron-rosbag-extractor"]="$EXTRACTOR_VERSION"
    ["subdron-image-processing"]="$IMAGE_PROCESSING_VERSION"
    ["subdron-colmap"]="$COLMAP_VERSION"
)

for name in "${!VERSIONS[@]}"; do
    version="${VERSIONS[$name]}"
    remote="$REGISTRY/$name/$name:$version"
    echo "--- $name -> $version ---"
    if [ -n "$DRY_RUN" ]; then
        echo "  would: docker pull $remote && docker tag $remote $name:dev"
        continue
    fi
    docker pull "$remote"
    docker tag "$remote" "$name:dev"
    docker image inspect --format "  $name:dev = {{index .RepoDigests 0}}" "$name:dev"
done

echo "Done. Runners will use these on their next launch."
