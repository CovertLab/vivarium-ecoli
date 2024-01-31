#!/usr/bin/env bash
# Build the wcm-runtime Docker container Image locally.
#
# ASSUMES: The current working dir is the vivarium-ecoli/ project root.
#
# Add the `docker build` option `--build-arg from=ABC` to name a different "FROM" image.

set -eu

WCM_RUNTIME=${USER}-wcm-runtime

# Docker image #1: The Python runtime environment.
docker build -f cloud/docker/runtime/Dockerfile -t "${WCM_RUNTIME}" \
  .
