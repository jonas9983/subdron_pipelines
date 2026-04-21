#!/bin/bash
set -e

# --- Configuration ---
ENV=""
BUILD_REQUIRED=""
TAG=""

echo "   Subdron Pipeline Deployment Orchestrator"

# 1. Select Environment
if [ -z "$ENV" ]; then
    echo "Select the target environment:"
    echo "1) Development (dev)"
    echo "2) Production (prod)"
    read -p "Selection [1-2]: " ENV_CHOICE
    case $ENV_CHOICE in
        1) ENV="dev" ;;
        2) ENV="prod" ;;
        *) echo "Invalid selection. Exiting."; exit 1 ;;
    esac
fi

# 2. Ask about Building
read -p "Do you want to Build and Push images before deploying? [Y/n]: " BUILD_CHOICE
BUILD_CHOICE="${BUILD_CHOICE:-y}"
if [[ "$BUILD_CHOICE" =~ ^[Yy]$ ]]; then
    BUILD_REQUIRED=true
else
    BUILD_REQUIRED=false
fi

# 3. Handle Tags and Execution
if [ "$ENV" == "dev" ]; then
    # For dev, we can have individual tags or one global one
    read -p "Enter a global TAG for ALL services (leave empty to specify individually): " GLOBAL_TAG
    
    if [ "$BUILD_REQUIRED" = true ]; then
        if [ -n "$GLOBAL_TAG" ]; then
            ./scripts/build_and_push.sh --tag "$GLOBAL_TAG"
        else
            ./scripts/build_and_push.sh
        fi
    fi

    echo "--- Proceeding to Deploy to DEV ---"
    if [ -n "$GLOBAL_TAG" ]; then
        ./scripts/deploy_dev.sh --bag-tag "$GLOBAL_TAG" --img-tag "$GLOBAL_TAG" --colmap-tag "$GLOBAL_TAG"
    else
        # deploy_dev.sh will ask for missing tags interactively if not provided
        ./scripts/deploy_dev.sh
    fi

else
    # For production, we always use a single release tag
    read -p "Enter the RELEASE TAG (e.g., v1.0.0 or production): " RELEASE_TAG
    if [ -z "$RELEASE_TAG" ]; then echo "Error: Release tag is required for production."; exit 1; fi

    if [ "$BUILD_REQUIRED" = true ]; then
        ./scripts/build_and_push.sh --tag "$RELEASE_TAG"
    fi

    echo "--- Proceeding to Deploy to PRODUCTION ---"
    ./scripts/deploy_prod.sh --tag "$RELEASE_TAG"
fi

echo "   Deployment Flow Completed Successfully!"
