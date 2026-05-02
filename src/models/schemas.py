"""
Modelos Pydantic compartidos por el clasificador-api.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# Códigos de los tipos de documento que el clasificador puede emitir.
TipoDocumento = Literal[
    "escritura",                # Escritura pública (compraventa, hipoteca, alzamiento)
    "cert_dominio_vigente",     # Certificado de dominio vigente (CBR)
    "cert_hipotecas_gravamenes",# Certificado de hipotecas y gravámenes (CBR)
    "cert_avaluo_sii",          # Certificado de avalúo fiscal (SII)
    "cert_municipal",           # Certificado municipal (número, no expropiación, recepción final)
    "plano_propiedad",          # Plano de propiedad / loteo
    "plan_regulador",           # Plan regulador comunal
    "otro",                     # No clasificado
]


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
