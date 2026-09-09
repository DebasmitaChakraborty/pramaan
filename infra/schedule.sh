#!/usr/bin/env bash
# Sets up the daily refresh -> sweep pipeline that keeps pramaan_demo's
# freshness rule (orders.created_at, 25h) truthful. thelook_ecommerce is
# continuously updated but pramaan_demo is a frozen copy of it, so without a
# periodic refresh the copy ages past the freshness threshold on its own --
# see README.md's "Rule design notes" section for how that was discovered.
#
# Two scheduled pieces, run 30 minutes apart so the refresh has finished
# before the sweep reads the table:
#   1. Cloud Run Job "pramaan-refresh"  -- re-runs scripts/bootstrap.py
#      against the live public dataset. Triggered daily by Cloud Scheduler
#      job "pramaan-refresh-daily" (0 20 * * * UTC = 01:30 IST) via the
#      Cloud Run Jobs REST :run endpoint, authenticated with an OAuth token
#      from a service account holding roles/run.invoker on the job.
#   2. Cloud Scheduler job "pramaan-sweep-daily" (30 20 * * * UTC) -- POSTs
#      straight to the live /sweep endpoint for orders. No OAuth needed;
#      /sweep is deployed --allow-unauthenticated.
#
# Idempotent: safe to re-run after infra/deploy.sh ships a new image, or to
# pick up a changed schedule/service account.
set -e

PROJECT_ID=${GCP_PROJECT_ID:-"pramaan-506517"}
REGION=${GOOGLE_CLOUD_LOCATION:-"us-central1"}
SERVICE_NAME="pramaan-engine"
REFRESH_JOB_NAME="pramaan-refresh"
REFRESH_SCHEDULER_NAME="pramaan-refresh-daily"
SWEEP_SCHEDULER_NAME="pramaan-sweep-daily"
REFRESH_TIMEOUT="900s"          # 15 min
REFRESH_CRON="0 20 * * *"       # 20:00 UTC = 01:30 IST
SWEEP_CRON="30 20 * * *"        # 20:30 UTC -- 30 min after the refresh

echo "Enabling required APIs..."
gcloud services enable run.googleapis.com cloudscheduler.googleapis.com \
  --project "${PROJECT_ID}"

SERVICE_IMAGE=$(gcloud run services describe "${SERVICE_NAME}" \
  --region "${REGION}" --project "${PROJECT_ID}" \
  --format='value(spec.template.spec.containers[0].image)')
SERVICE_ACCOUNT=$(gcloud run services describe "${SERVICE_NAME}" \
  --region "${REGION}" --project "${PROJECT_ID}" \
  --format='value(spec.template.spec.serviceAccountName)')
SERVICE_URL=$(gcloud run services describe "${SERVICE_NAME}" \
  --region "${REGION}" --project "${PROJECT_ID}" \
  --format='value(status.url)')

echo "Using service image:   ${SERVICE_IMAGE}"
echo "Using service account: ${SERVICE_ACCOUNT}"

JOB_ARGS=(
  --image="${SERVICE_IMAGE}"
  --region="${REGION}"
  --project="${PROJECT_ID}"
  --command=python
  --args=scripts/bootstrap.py,build
  --service-account="${SERVICE_ACCOUNT}"
  --set-env-vars="GCP_PROJECT_ID=${PROJECT_ID},GOOGLE_GENAI_USE_VERTEXAI=true,GOOGLE_CLOUD_LOCATION=${REGION}"
  --task-timeout="${REFRESH_TIMEOUT}"
  --max-retries=0
)

if gcloud run jobs describe "${REFRESH_JOB_NAME}" --region "${REGION}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
  echo "Updating Cloud Run Job [${REFRESH_JOB_NAME}]..."
  gcloud run jobs update "${REFRESH_JOB_NAME}" "${JOB_ARGS[@]}"
else
  echo "Creating Cloud Run Job [${REFRESH_JOB_NAME}]..."
  gcloud run jobs create "${REFRESH_JOB_NAME}" "${JOB_ARGS[@]}"
fi

echo "Granting roles/run.invoker on [${REFRESH_JOB_NAME}] to ${SERVICE_ACCOUNT}..."
gcloud run jobs add-iam-policy-binding "${REFRESH_JOB_NAME}" \
  --region "${REGION}" --project "${PROJECT_ID}" \
  --member="serviceAccount:${SERVICE_ACCOUNT}" \
  --role="roles/run.invoker" >/dev/null

REFRESH_RUN_URI="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/${REFRESH_JOB_NAME}:run"

if gcloud scheduler jobs describe "${REFRESH_SCHEDULER_NAME}" --location "${REGION}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
  echo "Updating Cloud Scheduler job [${REFRESH_SCHEDULER_NAME}]..."
  gcloud scheduler jobs update http "${REFRESH_SCHEDULER_NAME}" \
    --location "${REGION}" --project "${PROJECT_ID}" \
    --schedule="${REFRESH_CRON}" --time-zone="UTC" \
    --uri="${REFRESH_RUN_URI}" --http-method=POST \
    --oauth-service-account-email="${SERVICE_ACCOUNT}"
else
  echo "Creating Cloud Scheduler job [${REFRESH_SCHEDULER_NAME}]..."
  gcloud scheduler jobs create http "${REFRESH_SCHEDULER_NAME}" \
    --location "${REGION}" --project "${PROJECT_ID}" \
    --schedule="${REFRESH_CRON}" --time-zone="UTC" \
    --uri="${REFRESH_RUN_URI}" --http-method=POST \
    --oauth-service-account-email="${SERVICE_ACCOUNT}"
fi

if gcloud scheduler jobs describe "${SWEEP_SCHEDULER_NAME}" --location "${REGION}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
  echo "Updating Cloud Scheduler job [${SWEEP_SCHEDULER_NAME}]..."
  gcloud scheduler jobs update http "${SWEEP_SCHEDULER_NAME}" \
    --location "${REGION}" --project "${PROJECT_ID}" \
    --schedule="${SWEEP_CRON}" --time-zone="UTC" \
    --uri="${SERVICE_URL}/sweep" --http-method=POST \
    --headers="Content-Type=application/json" \
    --message-body='{"dataset":"pramaan_demo","table":"orders"}'
else
  echo "Creating Cloud Scheduler job [${SWEEP_SCHEDULER_NAME}]..."
  gcloud scheduler jobs create http "${SWEEP_SCHEDULER_NAME}" \
    --location "${REGION}" --project "${PROJECT_ID}" \
    --schedule="${SWEEP_CRON}" --time-zone="UTC" \
    --uri="${SERVICE_URL}/sweep" --http-method=POST \
    --headers="Content-Type=application/json" \
    --message-body='{"dataset":"pramaan_demo","table":"orders"}'
fi

echo "Scheduling setup complete."
echo "  gcloud run jobs execute ${REFRESH_JOB_NAME} --region ${REGION}      # run the refresh manually"
echo "  gcloud scheduler jobs run ${REFRESH_SCHEDULER_NAME} --location ${REGION}  # force a scheduled refresh"
echo "  gcloud scheduler jobs run ${SWEEP_SCHEDULER_NAME} --location ${REGION}    # force a scheduled sweep"
