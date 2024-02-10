#!/bin/bash
gcloud compute instances stop mongodb-vecoli
DISK_ZONE=$(gcloud compute disks describe mongodb-vecoli --format='get(zone)')
DATETIME=$(date +%D-%T)
gcloud compute snapshots create "mongodb-vecoli-$DATETIME" \
    --source-disk-zone=$DISK_ZONE \
    --source-disk=mongodb-vecoli
gcloud compute instances delete mongodb-vecoli
