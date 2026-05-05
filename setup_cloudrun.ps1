. "$PSScriptRoot\..\infra\config.ps1"

$SERVICE_BASE = "clasificador-api"
$SERVICE_NAME = Get-HipotecaiServiceName $SERVICE_BASE
$IMAGE        = "$AR_HOST/$PROJECT_ID/$AR_REPO/$($SERVICE_NAME):latest"
$SA_EMAIL     = "$SERVICE_BASE-sa@$PROJECT_ID.iam.gserviceaccount.com"

Write-Host "Ambiente    : $($Global:ENVIRONMENT.ToUpper())"
Write-Host "Servicio    : $SERVICE_NAME"
Write-Host "Imagen      : $IMAGE"
Write-Host "Service SA  : $SA_EMAIL"
Write-Host "Cloud SQL   : $INSTANCE_CONNECTION_NAME - base $DB_NAME"
Write-Host "Bucket      : gs://$GCS_BUCKET_DOCUMENTOS"

Write-Host "`n[1/2] Construyendo imagen..." -ForegroundColor Yellow
gcloud builds submit . --tag $IMAGE --project $PROJECT_ID
if ($LASTEXITCODE -ne 0) { exit 1 }

Write-Host "`n[2/2] Desplegando a Cloud Run..." -ForegroundColor Yellow

$nodeEnv = if ($Global:ENVIRONMENT -eq "prod") { "production" } else { "staging" }

gcloud run deploy $SERVICE_NAME `
  --image $IMAGE `
  --region $REGION `
  --project $PROJECT_ID `
  --platform managed `
  --service-account $SA_EMAIL `
  --allow-unauthenticated `
  --add-cloudsql-instances $INSTANCE_CONNECTION_NAME `
  --set-env-vars "NODE_ENV=$nodeEnv,GOOGLE_CLOUD_PROJECT=$PROJECT_ID,DB_USER=$DB_USER,DB_NAME=$DB_NAME,DB_PORT=$DB_PORT,INSTANCE_CONNECTION_NAME=$INSTANCE_CONNECTION_NAME,GOOGLE_CLIENT_ID=$GOOGLE_CLIENT_ID,ALLOWED_ORIGINS=$ALLOWED_ORIGINS,GCS_BUCKET_DOCUMENTOS=$GCS_BUCKET_DOCUMENTOS,GEMINI_MODEL_ID=gemini-2.5-flash,LANGEXTRACT_TEMPERATURE=0.1,OCR_API_URL=$($env:OCR_API_URL),OCR_API_TIMEOUT_S=300" `
  --set-secrets "DB_PASSWORD=$($SECRET_DB_PASSWORD):latest,GEMINI_API_KEY=$($SECRET_GEMINI_API_KEY):latest" `
  --port 8080 `
  --memory 2Gi `
  --cpu 2 `
  --timeout 300 `
  --concurrency 20 `
  --max-instances 10 `
  --min-instances 0

if ($LASTEXITCODE -eq 0) {
  $url = gcloud run services describe $SERVICE_NAME --region=$REGION --project=$PROJECT_ID --format="value(status.url)"
  Write-Host "`n[OK] Desplegado: $url" -ForegroundColor Green
  Write-Host "Health check: curl $url/health"
}
