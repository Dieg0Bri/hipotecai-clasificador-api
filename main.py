"""
clasificador-api · FastAPI principal
--------------------------------------------------------------
Endpoints:
  GET  /health
  POST /classify-text          ← clasifica un texto plano (UI / pruebas)
  POST /classify-from-gcs      ← descarga el objeto del bucket y lo clasifica
  POST /                       ← receptor Eventarc (compatibilidad)
"""
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from google.cloud import storage

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.config import settings  # noqa: E402
from src.core.structured_logging import configure_logging  # noqa: E402
from src.database.cloudsql_handler import CloudSQLHandler  # noqa: E402
from src.middleware.analytics import AnalyticsMiddleware  # noqa: E402
from src.middleware.oauth import GoogleOAuthMiddleware  # noqa: E402
from src.models.schemas import ClasificarFromGCSRequest, ClasificarTextoRequest  # noqa: E402
from src.services.classifier import ClassifierService  # noqa: E402
from src.services.text_extractor import extract_text_from_bytes  # noqa: E402
from src.utils.responses import error_response, success_response  # noqa: E402


configure_logging()
logger = logging.getLogger(__name__)

# Estado global
classifier: Optional[ClassifierService] = None
storage_client: Optional[storage.Client] = None
db: Optional[CloudSQLHandler] = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global classifier, storage_client, db

    logger.info("Starting clasificador-api...")

    # langextract / Gemini
    classifier = ClassifierService()
    if not settings.GEMINI_API_KEY:
        logger.warning("GEMINI_API_KEY no configurada — fallback heurístico activo.")
    else:
        logger.info("Classifier ready (gemini=%s)", settings.GEMINI_MODEL_ID)

    # GCS
    try:
        storage_client = storage.Client()
        logger.info("GCS client ok (bucket=%s)", settings.GCS_BUCKET_DOCUMENTOS)
    except Exception as exc:  # noqa: BLE001
        logger.warning("GCS init failed: %s", exc)

    # CloudSQL
    db = CloudSQLHandler()
    try:
        await db.initialize()
        if await db.health_check():
            logger.info("CloudSQL ok")
    except Exception as exc:  # noqa: BLE001
        logger.warning("CloudSQL init failed: %s", exc)

    yield

    if db:
        await db.close()


app = FastAPI(
    title="clasificador-api",
    description="Clasificador de documentos hipotecarios chilenos.",
    version=settings.SERVICE_VERSION,
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
)
app.add_middleware(AnalyticsMiddleware)
app.add_middleware(GoogleOAuthMiddleware)


# ─────────────────────────────── ENDPOINTS ───────────────────────────────

@app.get("/health")
async def health():
    db_ok = await db.health_check() if db else False
    return success_response(
        data={
            "service": settings.SERVICE_NAME,
            "version": settings.SERVICE_VERSION,
            "database": "ok" if db_ok else "error",
            "gemini_configured": bool(settings.GEMINI_API_KEY),
        }
    )


@app.post("/classify-text")
async def classify_text(req: ClasificarTextoRequest):
    if not classifier:
        raise HTTPException(503, "Classifier no inicializado")
    try:
        result = classifier.classify(req.texto)
        return success_response(data=result.model_dump())
    except Exception as exc:  # noqa: BLE001
        logger.exception("classify-text error")
        return error_response(message=str(exc), code="CLASSIFY_FAILED")


@app.post("/classify-from-gcs")
async def classify_from_gcs(req: ClasificarFromGCSRequest):
    if not classifier:
        raise HTTPException(503, "Classifier no inicializado")
    if not storage_client:
        return error_response("GCS no inicializado.", code="GCS_UNAVAILABLE", status_code=503)

    try:
        bucket = storage_client.bucket(settings.GCS_BUCKET_DOCUMENTOS)
        blob = bucket.blob(req.gcs_path)
        if not blob.exists():
            return error_response(f"Objeto no existe: {req.gcs_path}", code="OBJECT_NOT_FOUND", status_code=404)

        # Cargar y extraer texto
        content = blob.download_as_bytes()
        mime_type = blob.content_type
        text = extract_text_from_bytes(content, mime_type=mime_type, filename=req.gcs_path)

        if not text.strip():
            return error_response(
                "PDF sin texto seleccionable. Pasarlo por OCR antes de clasificar.",
                code="NO_TEXT",
                status_code=422,
            )

        result = classifier.classify(text)

        # Persistir clasificación en BDD
        if req.id_archivo and db:
            try:
                await db.update_archivo_clasificacion(req.id_archivo, result.tipo, result.confianza)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not update dt_archivos: %s", exc)

        return success_response(
            data={
                "folio": req.folio,
                "gcs_path": req.gcs_path,
                "clasificacion": result.model_dump(),
            },
            message=f"Documento clasificado como {result.nombre_largo} (confianza={result.confianza:.2%}).",
        )
    except NotImplementedError as exc:
        return error_response(str(exc), code="UNSUPPORTED_FORMAT", status_code=415)
    except Exception as exc:  # noqa: BLE001
        logger.exception("classify-from-gcs error")
        return error_response(message=str(exc), code="CLASSIFY_FAILED")


@app.post("/")
async def eventarc_receiver(request: Request):
    """
    Receptor de Eventarc (GCS object finalize).
    En modo Eventarc el body trae bucket/name; clasificamos y luego
    el resto del pipeline lo encadena documentos-api.
    """
    try:
        payload = await request.json()
        # Pub/Sub envuelto
        if isinstance(payload, dict) and "message" in payload and "data" in payload.get("message", {}):
            import base64
            import json as _json
            payload = _json.loads(base64.b64decode(payload["message"]["data"]).decode())

        bucket = payload.get("bucket")
        name = payload.get("name")
        if not bucket or not name:
            return success_response(message="ignored (no bucket/name)")

        if not classifier or not storage_client:
            return error_response("Servicio no listo", code="NOT_READY", status_code=503)

        blob = storage_client.bucket(bucket).blob(name)
        if not blob.exists():
            return success_response(message=f"object missing: {name}")

        content = blob.download_as_bytes()
        text = extract_text_from_bytes(content, mime_type=blob.content_type, filename=name)
        if not text.strip():
            logger.info("Eventarc: %s sin texto, requiere OCR", name)
            return success_response(message="needs OCR")

        result = classifier.classify(text)
        logger.info(
            "Eventarc clasificado %s → %s (%.2f), triggers=%s",
            name, result.tipo, result.confianza, result.triggers,
        )

        # Persistir clasificación en dt_archivos (si conocemos id_archivo)
        # y solicitudes generadas por triggers en dt_documentos_solicitados.
        # El path en GCS sigue el patrón <folio>/<filename>.
        folio = name.split("/")[0] if "/" in name else None

        # Re-evaluamos triggers para tener el detalle (codigos_solicitados, motivo)
        # — el classifier solo guardó los IDs en result.triggers.
        from src.services.triggers import evaluar_triggers
        solicitudes = evaluar_triggers(text=text, tipo=result.tipo)

        if db and folio and solicitudes:
            try:
                inserted = await db.save_documentos_solicitados(
                    folio=folio,
                    id_archivo_origen=None,  # ingestion-service tiene el id; aquí no
                    solicitudes=solicitudes,
                )
                logger.info("Eventarc: %d solicitudes guardadas para folio=%s", inserted, folio)
            except Exception as exc:  # noqa: BLE001
                logger.exception("save_documentos_solicitados falló (no bloqueante)")

        # TODO v0+1: publicar a Pub/Sub para encadenar documentos-api
        return success_response(data=result.model_dump())
    except Exception as exc:  # noqa: BLE001
        logger.exception("eventarc handler error")
        return error_response(message=str(exc), code="EVENTARC_FAILED")
