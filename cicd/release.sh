#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

VERSION=""

# Parse command line arguments
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --tag) VERSION="$2"; shift ;;
        --tag=*) VERSION="${1#*=}" ;;
        --target-branch) TARGET_BRANCH="$2"; shift ;;
        --target-branch=*) TARGET_BRANCH="${1#*=}" ;;
        *) echo " Unknown parameter passed: $1"; exit 1 ;;
    esac
    shift
done

TARGET_BRANCH="${TARGET_BRANCH:-unknown}"

# Check if a version was provided
if [ -z "$VERSION" ]; then
  echo " Error: Please provide a version number."
  echo " Usage: ./release.sh --tag v1.5.0"
  exit 1
fi

# Create the specific version tag
echo " Tagging commit as $VERSION..."
git tag -a "$VERSION" -m "Release version $VERSION"

# Push the tags to remote
echo " Pushing tags to remote..."
git push origin "$VERSION"

if [[ "$TARGET_BRANCH" == "development" ]]; then
    echo "Updating 'latest' tag..."
    git tag -f -a latest -m "Latest release ($VERSION)"
    git push -f origin latest
fi

echo " Successfully released $VERSION"