#!/bin/bash
set -eu
MONGO_DISK_SIZE_GB="${1:-2500}"
# Get VM name from account name (left of "@" in email)
gcp_account=$(gcloud config get-value account)
IFS='@' read -ra gcp_account <<< $gcp_account
gcp_account=${gcp_account[0]}
vm_name=${gcp_account}-mongodb
snapshot_data=$(gcloud compute snapshots list \
    --filter="name:'final-antibiotic*'" \
    --format="value(name,diskSizeGb)" \
    --sort-by="~creationTimestamp" 2>&1)
# If no snapshot exists, create fresh disk
if [ -z "${snapshot_data}" ]; then
    disk_arg="--create-disk=name=${vm_name}-data,size=${MONGO_DISK_SIZE_GB}GB,type=pd-balanced"
    echo "Creating new disk of size ${MONGO_DISK_SIZE_GB} GB."
# Otherwise, restore from most recently created snapshot
else
    echo "Restoring from snapshot ${snapshot_data[0]}" \
        "to disk of size ${snapshot_data[1]} GB."
    IFS=$'\t' read -ra snapshot_data <<< $snapshot_data
    gcloud compute disks create ${vm_name}-data \
        --size=${snapshot_data[1]} \
        --source-snapshot=${snapshot_data[0]}
    disk_arg="--disk=name=${vm_name}-data"
    echo "Done restoring from snapshot."
fi
# Create VM with latest MongoDB image
gcloud compute instances create-with-container ${vm_name} \
    --machine-type=e2-standard-2 \
    --boot-disk-size=10GB \
    --boot-disk-type=pd-balanced \
    ${disk_arg} \
    --container-command="/bin/bash" \
    --container-arg="-c" \
    --container-arg="chmod 777 /data/db; \
        docker-entrypoint.sh mongod" \
    --container-image=mongo:latest \
    --container-mount-disk=mount-path=/data/db \
    --no-address
echo "Done creating ${vm_name}."
