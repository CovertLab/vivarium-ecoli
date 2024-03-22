#!/bin/bash
set -eu
# Get VM name from account name (left of "@" in email)
gcp_account=$(gcloud config get-value account)
IFS='@' read -ra gcp_account <<< $gcp_account
gcp_account=${gcp_account[0]}
vm_name=${gcp_account}-mongodb
# Create snapshot of data disk
DISK_ZONE=$(gcloud compute disks describe ${vm_name}-data --format='get(zone)')
DATETIME=$(date +%D-%T)
gcloud compute snapshots create "${vm_name}-data-${DATETIME}" \
    --source-disk-zone=$DISK_ZONE \
    --source-disk=${vm_name}-data
# Only keep three most recent snapshots
snapshot_names=$(gcloud compute snapshots list \
    --filter="name:'${vm_name}-data-*'" \
    --format="value(name)" \
    --sort-by="~creationTimestamp" 2>&1)
IFS=$'\n' snapshot_names=($snapshot_names)
unset IFS
# Allow user to cancel shutdown sequence
read -p "Are you fully done with MongoDB? If so, "\
    "we will save a snapshot of the data and delete the VM."\
    "It is possible to restore from this snapshot by running"\
    "start_mongo.sh, but this is time-consuming."
num_snapshots=${#snapshot_names[@]}
if (( $num_snapshots > 3 )); then
    echo By default, we keep the 3 most recent snapshots.
    echo You have $num_snapshots saved snapshots.
    echo "From oldest to newest, choose whether to keep or" \
        "delete the following excess snapshots."
    for (( i = 0; i < $(($num_snapshots-3)); i++ )); do
        read -p "Keep ${snapshot_names[${i}]}? (y/N) " yn
        case $yn in
            [yY] ) echo Not deleting.;;
            [nN] ) echo Deleting.;
                gcloud compute snapshots delete "${snapshot_names[${i}]}";;
            * ) echo Invalid response, try again.;;
        esac
    done
fi
# Delete VM, which also deletes attached persistent disks
gcloud compute instances delete ${vm_name}
