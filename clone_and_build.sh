#!/usr/bin/env bash
set -euo pipefail

REPOS=(
  "git@bitbucket.org:subdron/colmap.git"
  "git@bitbucket.org:subdron/image-processing.git"
)

BRANCHES=(
  "feature/CD-85-create-docker-composite-that-inclu"
  "feature/CD-83-creating-docker-file-for-enhanceme"
)

TARGET="./"

echo "Starting service setup for underwater-vision-pipeline..."

for i in "${!REPOS[@]}"; do
  url="${REPOS[$i]}"
  branch="${BRANCHES[$i]}"
  name="$(basename "${url%.git}")"
  dir="${TARGET}${name}"
  
  if [ -d "$dir" ]; then
    echo "🔄 Updating existing repo '$name'..."
    git -C "$dir" pull
  else
    echo "⬇️ Cloning '$name' from $url with branch '$branch'..."
    git clone --single-branch --branch "$branch" "$url" "$dir"
  fi
  
  # Check if the directory is 'image-processing' and update submodules
  if [ "$name" == "image-processing" ]; then
    echo "⚙️ Updating submodules for '$name'..."
    git -C "$dir" submodule update --init --recursive
  fi
done

echo "✅ All services cloned/updated."