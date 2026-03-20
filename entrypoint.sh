#!/bin/bash
set -e

# Create download directory if it doesn't exist
if [ ! -d "${DOWNLOAD_PATH}" ]; then
    echo "Creating download directory: ${DOWNLOAD_PATH}"
    mkdir -p "${DOWNLOAD_PATH}"
fi

# Build CLI arguments from environment variables
ARGS="--action ${ACTION}"
ARGS="${ARGS} --mode ${DOWNLOAD_MODE}"
ARGS="${ARGS} --workers ${WORKERS}"
ARGS="${ARGS} --start-page ${START_PAGE}"
ARGS="${ARGS} --pages ${PAGES}"
ARGS="${ARGS} --per-page ${PER_PAGE}"
ARGS="${ARGS} --download-path ${DOWNLOAD_PATH}"
ARGS="${ARGS} --chunk-size ${CHUNK_SIZE}"
ARGS="${ARGS} --progress-mode ${PROGRESS_MODE}"
ARGS="${ARGS} --retry-count ${RETRY_COUNT}"
ARGS="${ARGS} --retry-delay ${RETRY_DELAY}"

if [ "${VERBOSE}" = "true" ]; then
    ARGS="${ARGS} --verbose"
fi

# Note: UPLOAD_* env vars are read directly by Python, no CLI mapping needed

exec python3 main.py ${ARGS}
