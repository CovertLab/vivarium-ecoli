#!/bin/sh
# Use Google Cloud Build or local Docker install to build a personalized
# image with requirements.txt installed. If using Cloud Build, store the
# built image in the "vecoli" folder in the Google Artifact Registry.
#
# ASSUMES: The current working dir is the vivarium-ecoli/ project root.

set -eu

RUNTIME_IMAGE="${USER}-wcm-runtime"
RUN_LOCAL='false'

usage_str="Usage: build-runtime.sh [-r RUNTIME_IMAGE] [-l]\n\
    -r: Docker tag for the wcm-runtime image to build; defaults to \
${USER}-wcm-runtime\n\
    -l: Build image locally.\n"

print_usage() {
  printf "$usage_str"
}

while getopts 'r:l' flag; do
  case "${flag}" in
    r) RUNTIME_IMAGE="${OPTARG}" ;;
    l) RUN_LOCAL="${OPTARG}" ;;
    *) print_usage
       exit 1 ;;
  esac
done

# This needs only one payload file so copy it in rather than using a config at
# the project root which would upload the entire project.
cp requirements.txt scripts/container/runtime/

if [ "$RUN_LOCAL" = true ]; then
    echo "=== Locally building WCM runtime Docker Image: ${RUNTIME_IMAGE} ==="
    docker build -f scripts/container/runtime/Dockerfile -t "${WCM_RUNTIME}" .
else
    PROJECT="$(gcloud config get-value core/project)"
    REGION=$(gcloud config get compute/region)
    TAG="${REGION}-docker.pkg.dev/${PROJECT}/vecoli/${RUNTIME_IMAGE}"
    echo "=== Cloud-building WCM runtime Docker Image: ${TAG} ==="
    echo $TAG
    # This needs a config file to identify the project files to upload and the
    # Dockerfile to run.
    gcloud builds submit --timeout=3h --tag "${TAG}" scripts/container/runtime/
fi

rm scripts/container/runtime/requirements.txt
