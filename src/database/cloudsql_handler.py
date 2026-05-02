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

    async def close(self) -> None:
        if self.engine:
            await self.engine.dispose()
