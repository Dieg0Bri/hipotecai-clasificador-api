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
from src.services.text_extractor import (  # noqa: E402
    assemble_text_and_offsets,
    extract_pages_from_bytes,
    extract_text_from_bytes,
    page_stats as compute_page_stats,
)
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


CLASSIFICATION_PIPELINE_VERSION = "v1"


@app.post("/")
async def eventarc_receiver(request: Request):
    """
    Receptor de Eventarc (GCS object finalize).
    En modo Eventarc el body trae bucket/name; clasificamos y luego
    el resto del pipeline lo encadena documentos-api.

    Idempotencia (#6): Eventarc puede reentregar el mismo evento. Antes de
    clasificar verificamos `dt_pipeline_run` — si ya hay un OK para este
    (archivo, sha256, version), devolvemos sin re-pegarle a Gemini.
    """
    import hashlib
    import time

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
        sha256_documento = hashlib.sha256(content).hexdigest()

        # Resolver el id_archivo desde gcs_path (Eventarc no lo trae) para
        # poder consultar idempotencia y registrar el run.
        archivo_id_pre: int | None = None
        if db:
            try:
                archivo_id_pre = await db.find_archivo_by_gcs_path(name)
            except Exception:  # noqa: BLE001
                archivo_id_pre = None

        if db and archivo_id_pre is not None:
            already = await db.pipeline_run_already_ok(
                id_archivo=archivo_id_pre,
                etapa="clasificacion",
                sha256_input=sha256_documento,
                version=CLASSIFICATION_PIPELINE_VERSION,
            )
            if already:
                logger.info("Eventarc idempotente: %s ya clasificado para sha=%s",
                            name, sha256_documento[:12])
                return success_response(
                    message="idempotente: ya clasificado",
                    data={"gcs_path": name, "skipped": True},
                )

        # 1. Extracción nativa con pdfplumber + estadísticas por página.
        #    NO mezclamos OCR acá. Gemini decide más adelante si vale la pena.
        pages_local = extract_pages_from_bytes(
            content, mime_type=blob.content_type, filename=name,
        )
        stats = compute_page_stats(pages_local)
        text, _offsets = assemble_text_and_offsets(pages_local)

        # 2. Clasificación (Gemini ve el texto disponible + stats por página y
        #    decide tipo + requiere_ocr en una sola llamada). Si el documento
        #    no tenía NINGÚN texto extractable, classify() devuelve un
        #    placeholder con requiere_ocr=True para gatillar OCR primero.
        started = time.monotonic()
        result = classifier.classify(text, page_stats=stats)
        duracion_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "Eventarc clasificado %s → %s (%.2f) requiere_ocr=%s",
            name, result.tipo, result.confianza, result.requiere_ocr,
        )

        # 3. Si Gemini decidió OCR, lo gatillamos sincronamente (cubre el caso
        #    de documentos mixtos: carátula digital + cuerpo escaneado).
        #    Resultado: el .md vive en su bucket y dt_archivos.fuente_extraccion
        #    queda en 'ocr' antes de persistir la clasificación.
        fuente_extraccion = "pdf_text"
        estado_ocr = "no_aplica"

        if result.requiere_ocr and settings.OCR_API_URL:
            # Marcar pendiente antes de llamar al servicio (visible al letrado).
            if db and archivo_id_pre is not None:
                try:
                    await db.set_estado_ocr(archivo_id_pre, "procesando")
                except Exception:  # noqa: BLE001
                    pass
            try:
                import httpx
                async with httpx.AsyncClient(timeout=settings.OCR_API_TIMEOUT_S) as client:
                    ocr_resp = await client.post(
                        f"{settings.OCR_API_URL.rstrip('/')}/ocr-from-gcs",
                        json={"gcs_path": name, "id_archivo": archivo_id_pre},
                    )
                if ocr_resp.is_success:
                    body = ocr_resp.json().get("data") or {}
                    fuente_extraccion = "ocr"
                    estado_ocr = "listo"
                    # Si la primera clasificación fue placeholder ('otro' por
                    # falta de texto), re-clasificamos ahora con el texto OCR.
                    if result.tipo == "otro" and result.confianza == 0.0:
                        ocr_text = body.get("text") or ""
                        if ocr_text.strip():
                            result = classifier.classify(ocr_text, page_stats=None)
                            result.requiere_ocr = True
                            result.razon_ocr = "Re-clasificado tras OCR (documento sin texto nativo)."
                            logger.info(
                                "Re-clasificación post-OCR %s → %s (%.2f)",
                                name, result.tipo, result.confianza,
                            )
                else:
                    estado_ocr = "error"
                    logger.warning("OCR falló (%s): %s",
                                   ocr_resp.status_code, ocr_resp.text[:200])
            except Exception:  # noqa: BLE001
                estado_ocr = "error"
                logger.exception("OCR sync exception")
        elif result.requiere_ocr and not settings.OCR_API_URL:
            estado_ocr = "pendiente"  # OCR_API_URL no configurado; queda flagged
            logger.warning("requiere_ocr=True pero OCR_API_URL no configurado.")

        # 4. Persistir clasificación + decisión OCR en una transacción.
        id_archivo: int | None = None
        if db:
            try:
                id_archivo = await db.update_archivo_clasificacion_by_gcs_path(
                    gcs_path=name,
                    codigo_clasificacion=result.tipo,
                    confianza=float(result.confianza),
                    requiere_ocr=result.requiere_ocr,
                    razon_ocr=result.razon_ocr,
                    fuente_extraccion=fuente_extraccion,
                    estado_ocr=estado_ocr,
                    pages_stats=stats,
                )
                if id_archivo:
                    logger.info(
                        "Eventarc: dt_archivos id=%s clasificado (fuente=%s, estado_ocr=%s)",
                        id_archivo, fuente_extraccion, estado_ocr,
                    )
                else:
                    logger.warning("Eventarc: no se encontró dt_archivos con gcs_path=%s", name)
            except Exception:  # noqa: BLE001
                logger.exception("Eventarc: update_archivo_clasificacion_by_gcs_path falló")

        # Persistir solicitudes generadas por triggers en dt_documentos_solicitados.
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
                    id_archivo_origen=id_archivo,  # ahora sí lo tenemos
                    solicitudes=solicitudes,
                )
                logger.info("Eventarc: %d solicitudes guardadas para folio=%s", inserted, folio)
            except Exception as exc:  # noqa: BLE001
                logger.exception("save_documentos_solicitados falló (no bloqueante)")

        # Registrar idempotencia exitosa (#6).
        if db and id_archivo is not None:
            try:
                await db.pipeline_run_record(
                    id_archivo=id_archivo,
                    etapa="clasificacion",
                    sha256_input=sha256_documento,
                    version=CLASSIFICATION_PIPELINE_VERSION,
                    estado="ok",
                    duracion_ms=duracion_ms,
                )
            except Exception:  # noqa: BLE001
                logger.exception("pipeline_run_record (ok) falló — no bloqueante")

        # TODO v0+1: publicar a Pub/Sub para encadenar documentos-api
        return success_response(data=result.model_dump())
    except Exception as exc:  # noqa: BLE001
        logger.exception("eventarc handler error")
        return error_response(message=str(exc), code="EVENTARC_FAILED")
