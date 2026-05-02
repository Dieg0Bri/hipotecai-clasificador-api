"""
Modelos Pydantic compartidos por el clasificador-api.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.models.catalogo import CODIGOS_VALIDOS

# El tipo se valida contra el catálogo en runtime via Pydantic.
# Lo dejamos como str + validator (en vez de Literal[...]) para no
# tener que duplicar la lista en dos lugares.
TipoDocumento = str  # validar con CODIGOS_VALIDOS


class ClasificarFromGCSRequest(BaseModel):
    """Request del clasificador cuando el archivo ya está en GCS."""
    model_config = ConfigDict(extra="forbid")

    folio: str = Field(..., description="Folio del estudio (EH-YYYY-NNNN).")
    gcs_path: str = Field(..., description="Path del objeto en el bucket de hipotecai (sin gs://).")
    id_archivo: int | None = Field(None, description="ID del registro en dt_archivos para actualizar.")


class ClasificarTextoRequest(BaseModel):
    """Request directo con el texto del documento (modo prueba/UI)."""
    model_config = ConfigDict(extra="forbid")

    texto: str = Field(..., min_length=10)
    nombre: str | None = None


class ClasificacionResultado(BaseModel):
    """Resultado de la clasificación: tipo + score + spans relevantes."""
    model_config = ConfigDict(extra="allow")

    tipo: TipoDocumento
    confianza: float = Field(..., ge=0.0, le=1.0)
    nombre_largo: str
    razones: list[str] = []
    spans_evidencia: list[dict] = Field(
        default_factory=list,
        description="Fragmentos del texto fuente que justifican la clasificación.",
    )
    requiere_revision: bool = False
    triggers: list[str] = Field(
        default_factory=list,
        description="IDs de triggers IF/THEN que se gatillaron por contenido del documento.",
    )


# Re-exportar variantes literal para compatibilidad con código existente.
SeveridadHallazgo = Literal["critica", "alta", "media", "baja", "info"]
