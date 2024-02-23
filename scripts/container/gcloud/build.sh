#!/bin/sh
# Use Google Cloud Build servers to build layered WCM Docker Images and store
# them in the Google Container Registry.
#
# ASSUMES: The current working dir is the vivarium-ecoli/ project root.

set -eu

# 1. The Python runtime environment with the default user-specific runtime tag.
scripts/container/gcloud/build-runtime.sh

# 2. The Whole Cell Model code with the default user-specific wcm tag,
# building FROM the default user-specific runtime.
scripts/container/gcloud/build-wcm.sh
