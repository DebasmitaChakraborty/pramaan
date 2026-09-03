#!/usr/bin/env bash
set -e

PROJECT_ID=${GCP_PROJECT_ID:-"pramaan-506517"}
REGION=${GOOGLE_CLOUD_LOCATION:-"us-central1"}
SERVICE_NAME="pramaan-engine"

echo "Building container image in Artifact Registry / Cloud Build..."
gcloud builds submit --tag "gcr.io/${PROJECT_ID}/${SERVICE_NAME}:latest" .

echo "Deploying to Cloud Run with Service Account IAM controls..."
gcloud run deploy ${SERVICE_NAME} \
  --image "gcr.io/${PROJECT_ID}/${SERVICE_NAME}:latest" \
  --platform managed \
  --region ${REGION} \
  --no-allow-unauthenticated \
  --set-env-vars "GCP_PROJECT_ID=${PROJECT_ID},GOOGLE_GENAI_USE_VERTEXAI=true,GOOGLE_CLOUD_LOCATION=${REGION}" \
  --memory 1Gi \
  --concurrency 80 \
  --timeout 300s

echo "Cloud Run Deployment Complete!"
