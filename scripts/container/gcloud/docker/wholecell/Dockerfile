# Container image #2: wcm-code.
# This Dockerfile builds a container image with the vivarium-ecoli whole cell model
# code, layered on the wcm-runtime container image.
#
# To build this image locally from the vivarium-ecoli/ project root directory:
#
#     > docker build -f cloud/docker/wholecell/Dockerfile -t ${USER}-wcm-code --build-arg from=${USER}-wcm-runtime .
#
# (If you want to build container image #3 on top of the locally-built base,
# you'll have to edit that Dockerfile or upload this image.)
#
# (To build using the Cloud Build service and store in the Container Registry,
# run `cloud/build.sh`.)
#
# After building locally you can start up a new container from the image:
#
#     > docker run --name wholecelltest -it --rm ${USER}-wcm-code
#
# or if you used build.sh or build-wcm.sh to build using Cloud Build:
#
#     > PROJECT="$(gcloud config get-value core/project)"
#     > TAG="gcr.io/${PROJECT}/${USER}-wcm-code"
#     > docker run --name wholecelltest -it --rm "${TAG}"
#
# It will start a shell where you can execute commands:
#
#     # pytest
#
# If this succeeds you can start running WCM code in the container, e.g.:
#
#     # python runscripts/manual/runParca.py

ARG from=wcm-runtime:latest
FROM ${from}

# Record Docker Image metadata in ENV variables, viewable by `docker inspect`
# and accessible to programs in the container.
ARG git_hash=""
ARG git_branch=""
ARG timestamp=""
ENV IMAGE_GIT_HASH="$git_hash" \
	IMAGE_GIT_BRANCH="$git_branch" \
	IMAGE_TIMESTAMP="$timestamp"

LABEL application="Whole Cell Model of Escherichia coli" \
    email="allencentercovertlab@gmail.com" \
    license="https://github.com/CovertLab/vivarium-ecoli/blob/master/LICENSE" \
    organization="Covert Lab at Stanford" \
    website="https://www.covert.stanford.edu/"

COPY . /vivarium-ecoli
WORKDIR /vivarium-ecoli

RUN make clean compile
ENV PYTHONPATH=/vivarium-ecoli

# Since this build runs as root, set permissions so running the container as
# another user will work: Aesara needs to write into the data dir it uses when
# running as a user with no home dir, and Parca writes into /vivarium-ecoli/cache/.
RUN (umask 000 && mkdir -p /.aesara /vivarium-ecoli/cache)

CMD ["/bin/bash"]
