"""
CloudSQLHandler · acceso a Postgres con SQLAlchemy async.
Detecta Cloud Run y usa Unix socket de Cloud SQL Proxy.
"""
import logging
import os
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy import text

from src.core.config import settings

logger = logging.getLogger(__name__)


class CloudSQLHandler:
    def __init__(self) -> None:
        self.engine: Optional[AsyncEngine] = None
        self.session_factory = None

    def _build_url(self) -> str:
        if os.environ.get("K_SERVICE") and settings.INSTANCE_CONNECTION_NAME:
            return (
                f"postgresql+asyncpg://{settings.DB_USER}:{settings.DB_PASSWORD}"
                f"@/{settings.DB_NAME}?host=/cloudsql/{settings.INSTANCE_CONNECTION_NAME}"
            )
        return (
            f"postgresql+asyncpg://{settings.DB_USER}:{settings.DB_PASSWORD}"
            f"@{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}"
        )

    async def initialize(self) -> None:
        url = self._build_url()
        self.engine = create_async_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=2)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        logger.info("CloudSQL engine initialized")

    async def health_check(self) -> bool:
        if not self.engine:
            return False
        try:
            async with self.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("CloudSQL health check failed: %s", exc)
            return False

    async def update_archivo_clasificacion(
        self, id_archivo: int, codigo_clasificacion: str, confianza: float
    ) -> bool:
        if not self.engine:
            return False
        sql = text(
            """
            UPDATE dt_archivos
            SET id_clasificacion = (SELECT id FROM dt_clasificaciones WHERE codigo = :codigo),
                clasificacion_confianza = :confianza,
                estado_procesamiento = 'clasificado',
                fecha_actualizacion = NOW()
            WHERE id_archivo = :id_archivo
            """
        )
        async with self.session_factory() as session:
            await session.execute(sql, {"codigo": codigo_clasificacion, "confianza": confianza, "id_archivo": id_archivo})
            await session.commit()
        return True

    async def update_archivo_clasificacion_by_gcs_path(
        self,
        gcs_path: str,
        codigo_clasificacion: str,
        confianza: float,
        *,
        requiere_ocr: bool = False,
        razon_ocr: str | None = None,
        fuente_extraccion: str = "pdf_text",
        estado_ocr: str = "no_aplica",
        pages_stats: dict | None = None,
    ) -> Optional[int]:
        """
        Variante para el handler de Eventarc: el evento de GCS solo trae bucket+name,
        no el id_archivo. Lo buscamos por gcs_path y actualizamos. Devuelve el
        id_archivo actualizado o None si no se encontró match.

        Persiste también la decisión OCR (#008): el clasificador es quien
        decide si el documento necesita OCR; el extractor luego elige la
        fuente correcta sin tener que re-evaluar.
        """
        if not self.engine:
            return None
        import json
        sql = text(
            """
            UPDATE dt_archivos
            SET id_clasificacion = (SELECT id FROM dt_clasificaciones WHERE codigo = :codigo),
                clasificacion_confianza = :confianza,
                requiere_ocr = :requiere_ocr,
                razon_ocr = :razon_ocr,
                fuente_extraccion = :fuente_extraccion,
                estado_ocr = :estado_ocr,
                pages_stats = CAST(:pages_stats AS JSONB),
                estado_procesamiento = 'clasificado',
                fecha_actualizacion = NOW()
            WHERE gcs_path = :gcs_path AND eliminado = FALSE
            RETURNING id_archivo
            """
        )
        async with self.session_factory() as session:
            res = await session.execute(sql, {
                "codigo": codigo_clasificacion,
                "confianza": confianza,
                "requiere_ocr": requiere_ocr,
                "razon_ocr": razon_ocr,
                "fuente_extraccion": fuente_extraccion,
                "estado_ocr": estado_ocr,
                "pages_stats": json.dumps(pages_stats) if pages_stats else None,
                "gcs_path": gcs_path,
            })
            row = res.first()
            await session.commit()
            return row[0] if row else None

    async def set_estado_ocr(self, id_archivo: int, estado: str) -> None:
        """Cambia estado_ocr durante el ciclo del job (pendiente → procesando → listo/error)."""
        if not self.engine:
            return
        async with self.session_factory() as session:
            await session.execute(
                text(
                    "UPDATE dt_archivos SET estado_ocr = :e, fecha_actualizacion = NOW() "
                    "WHERE id_archivo = :id"
                ),
                {"e": estado, "id": id_archivo},
            )
            await session.commit()

    async def save_documentos_solicitados(
        self,
        folio: str,
        id_archivo_origen: Optional[int],
        solicitudes: list[dict],
    ) -> int:
        """
        Persiste las solicitudes que generaron los triggers IF/THEN.
        Una solicitud puede pedir múltiples códigos del catálogo (ej. T-CONDOMINIO
        pide cert_deuda_gastos_comunes Y acta_asamblea_copropietarios).

        Idempotente vía UNIQUE (id_estudio, trigger_id, id_clasificacion):
        si ya existe, no duplica.

        Returns: cantidad de filas efectivamente insertadas.
        """
        if not self.engine or not solicitudes:
            return 0

        sql = text(
            """
            INSERT INTO dt_documentos_solicitados (
                id_estudio, id_clasificacion, trigger_id, motivo,
                id_archivo_origen, matched_phrase, estado
            )
            SELECT
                e.id_estudio,
                c.id,
                :trigger_id,
                :motivo,
                :id_archivo_origen,
                :matched_phrase,
                'pendiente'
            FROM dt_estudio e, dt_clasificaciones c
            WHERE e.folio = :folio AND c.codigo = :codigo
            ON CONFLICT (id_estudio, trigger_id, id_clasificacion) DO NOTHING
            """
        )

        inserted = 0
        async with self.session_factory() as session:
            for sol in solicitudes:
                for codigo in sol.get("codigos_solicitados", []):
                    res = await session.execute(
                        sql,
                        {
                            "folio": folio,
                            "codigo": codigo,
                            "trigger_id": sol["trigger_id"],
                            "motivo": sol["motivo"],
                            "id_archivo_origen": id_archivo_origen,
                            "matched_phrase": (sol.get("matched_phrase") or "")[:500],
                        },
                    )
                    inserted += res.rowcount or 0
            await session.commit()
        return inserted

    # ───── Idempotencia de pipeline (#6) ─────

    async def pipeline_run_already_ok(
        self, *, id_archivo: int, etapa: str, sha256_input: str, version: str
    ) -> bool:
        """True si esta etapa ya corrió OK para este (archivo, sha, versión)."""
        if not self.engine:
            return False
        async with self.session_factory() as session:
            res = await session.execute(
                text(
                    """
                    SELECT 1 FROM dt_pipeline_run
                    WHERE id_archivo = :id AND etapa = :etapa
                      AND sha256_input = :sha AND version_pipeline = :version
                      AND estado = 'ok'
                    LIMIT 1
                    """
                ),
                {"id": id_archivo, "etapa": etapa, "sha": sha256_input, "version": version},
            )
            return res.first() is not None

    async def pipeline_run_record(
        self,
        *,
        id_archivo: int,
        etapa: str,
        sha256_input: str,
        version: str,
        estado: str,
        duracion_ms: int | None = None,
        error_msg: str | None = None,
        request_id: str | None = None,
    ) -> None:
        if not self.engine:
            return
        async with self.session_factory() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO dt_pipeline_run (
                        id_tenant, id_archivo, etapa, sha256_input, version_pipeline,
                        estado, duracion_ms, error_msg, request_id, started_at, finished_at
                    )
                    SELECT a.id_tenant, :id, :etapa, :sha, :version,
                           :estado, :dur, :err, :rid,
                           NOW() - (COALESCE(:dur,0) * INTERVAL '1 millisecond'), NOW()
                    FROM dt_archivos a WHERE a.id_archivo = :id
                    ON CONFLICT (id_archivo, etapa, sha256_input, version_pipeline, estado)
                    DO NOTHING
                    """
                ),
                {
                    "id": id_archivo, "etapa": etapa, "sha": sha256_input,
                    "version": version, "estado": estado, "dur": duracion_ms,
                    "err": (error_msg or "")[:1000] or None, "rid": request_id,
                },
            )
            await session.commit()

    async def find_archivo_by_gcs_path(self, gcs_path: str) -> int | None:
        if not self.engine:
            return None
        async with self.session_factory() as session:
            res = await session.execute(
                text("SELECT id_archivo FROM dt_archivos WHERE gcs_path = :p AND eliminado = FALSE"),
                {"p": gcs_path},
            )
            row = res.first()
            return row[0] if row else None

    async def close(self) -> None:
        if self.engine:
            await self.engine.dispose()
