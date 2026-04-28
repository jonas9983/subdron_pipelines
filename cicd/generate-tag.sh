#!/bin/bash

# Get latest tag
VERSION=$(git tag -l "v*" | sort -V | tail -n 1)

# Fallback if no tags exist
if [[ -z "$VERSION" ]]; then
    VERSION="0.0.0"
fi

# Strip leading 'v' or other non-digits for the calculation
PREFIX=$(echo "$VERSION" | grep -o '^[^0-9]*')
CLEAN_VERSION=$(echo "$VERSION" | sed 's/^[^0-9]*//')

# Split the CLEANED version into the array
IFS='.' read -ra ADDR <<< "$CLEAN_VERSION"
MAJOR=${ADDR[0]:-0}
MINOR=${ADDR[1]:-0}
PATCH=${ADDR[2]:-0}

# Initialize variables
TARGET_BRANCH=""
SOURCE_BRANCH=""

# Parse arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    -t|--target) TARGET_BRANCH="$2"; shift 2 ;;
    -s|--source) SOURCE_BRANCH="$2"; shift 2 ;;
    *) echo "Error: Unknown option $1" >&2; exit 1 ;;
  esac
done

if [[ -z "$TARGET_BRANCH" || -z "$SOURCE_BRANCH" ]]; then
    echo "Usage Error: Provide --target and --source." >&2
    exit 1
fi

SUFFIX=""

# Increment logic
if [[ "$TARGET_BRANCH" == "master" ]]; then
    MAJOR=$((MAJOR + 1))
    MINOR=0
    PATCH=0
elif [[ "$TARGET_BRANCH" == "development" ]]; then
    if [[ "$SOURCE_BRANCH" == feature/* ]]; then
        MINOR=$((MINOR + 1))
        PATCH=0
    elif [[ "$SOURCE_BRANCH" == bugfix/* || "$SOURCE_BRANCH" == hotfix/* ]]; then
        PATCH=$((PATCH + 1))
    else
        PATCH=$((PATCH + 1))
    fi
elif [[ "$TARGET_BRANCH" == "test-branch" ]]; then
    if [[ "$SOURCE_BRANCH" == feature/* ]]; then
        MINOR=$((MINOR + 1))
        PATCH=0
    elif [[ "$SOURCE_BRANCH" == bugfix/* || "$SOURCE_BRANCH" == hotfix/* ]]; then
        PATCH=$((PATCH + 1))
    else
        PATCH=$((PATCH + 1))
    fi
    SUFFIX=".test"
fi

if [[ "$TARGET_BRANCH" == "master" ]]; then
    PREFIX="uv"
else
    PREFIX="v"
fi

NEW_TAG="${PREFIX}${MAJOR}.${MINOR}.${PATCH}${SUFFIX}"

# Output Logic
echo "Found Latest Tag: $VERSION" >&2
echo "Target: $TARGET_BRANCH | Source: $SOURCE_BRANCH" >&2
echo "Calculated New Tag: $NEW_TAG" >&2

echo "$NEW_TAG"