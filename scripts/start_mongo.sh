#!/bin/bash
# Read MongoDB password
echo -n Create a password for MongoDB:
read -s password
echo -e \n
# Set MongoDB authentication environment variables
CONTAINER_ENV="MONGO_INITDB_ROOT_USERNAME=vecoli"
CONTAINER_ENV="$CONTAINER_ENV,MONGO_INITDB_ROOT_PASSWORD=$password"

gcloud compute instances create-with-container mongodb-vecoli \
    --zone=us-central1-a \
    --container-env=$CONTAINER_ENV \
    --machine-type=e2-standard-16 \
    --boot-disk-size=10GB \
    --boot-disk-type=pd-balanced \
    --create-disk=name=mongodb-vecoli-data,size=2500GB,type=pd-balanced \
    --container-arg=--dbpath=/scratch \
    --container-image=mongo:latest \
    --container-mount-disk=name=mongodb-vecoli-data,mount-path=/scratch
gcloud compute instances start mongodb-vecoli
