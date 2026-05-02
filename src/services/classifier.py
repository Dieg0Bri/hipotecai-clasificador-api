"""
classifier · clasificación de documentos hipotecarios chilenos vía langextract.

Usa langextract.extract con un prompt few-shot para que Gemini devuelva
estructuradamente:
  - tipo (uno de los TipoDocumento del dominio chileno)
  - confianza (0-1)
  - razones (texto)
  - spans_evidencia (fragmentos del texto fuente)

langextract garantiza que cada extracción esté anclada al texto original,
lo que el frontend usa para el resaltado y la auditoría legal.
"""
import logging
from typing import Any

from src.core.config import settings
from src.models.schemas import ClasificacionResultado, TipoDocumento

logger = logging.getLogger(__name__)

# Tipos del catálogo legal chileno con descripción para que el modelo
# diferencie con precisión.
TIPOS_CATALOGO: dict[TipoDocumento, dict[str, str]] = {
    "escritura": {
        "nombre_largo": "Escritura pública",
        "pista": "documento notarial firmado ante notario, con foja, repertorio y comparecientes",
    },
    "cert_dominio_vigente": {
        "nombre_largo": "Certificado de dominio vigente",
        "pista": "emitido por el Conservador de Bienes Raíces (CBR), indica titular actual",
    },
    "cert_hipotecas_gravamenes": {
        "nombre_largo": "Certificado de hipotecas y gravámenes",
        "pista": "emitido por el CBR, lista hipotecas, prohibiciones y limitaciones",
    },
    "cert_avaluo_sii": {
        "nombre_largo": "Certificado de avalúo fiscal",
        "pista": "emitido por el Servicio de Impuestos Internos (SII), incluye rol, avalúo total, exento",
    },
    "cert_municipal": {
        "nombre_largo": "Certificado municipal",
        "pista": "emitido por la Dirección de Obras Municipales (DOM): número, no expropiación, recepción final",
    },
    "plano_propiedad": {
        "nombre_largo": "Plano de propiedad / loteo",
        "pista": "plano arquitectónico/topográfico con cotas, deslindes y superficie",
    },
    "plan_regulador": {
        "nombre_largo": "Plan regulador comunal",
        "pista": "instrumento de planificación territorial con zonificación y normativa urbana",
    },
}


def _build_prompt() -> str:
    return (
        "Eres un asistente experto en derecho de propiedad raíz chileno. "
        "Clasifica el siguiente documento en una de estas categorías:\n\n"
        + "\n".join(f"- {k}: {v['pista']}" for k, v in TIPOS_CATALOGO.items())
        + "\n- otro: si no encaja en ninguna de las anteriores.\n\n"
        "Devuelve la clasificación junto con fragmentos textuales del documento "
        "que justifiquen tu decisión (autoridad emisora, encabezado, sellos, etc)."
    )


def _build_examples() -> list[dict[str, Any]]:
    """Few-shot examples para langextract. Documentos cortos representativos."""
    return [
        {
            "text": (
                "REPÚBLICA DE CHILE\nSERVICIO DE IMPUESTOS INTERNOS\n"
                "Certificado de Avalúo Fiscal\nRol de avalúo: 12345-7\n"
                "Comuna: Las Condes\nAvalúo total: $ 145.230.000\n"
            ),
            "extractions": [
                {
                    "extraction_class": "tipo_documento",
                    "extraction_text": "cert_avaluo_sii",
                    "attributes": {
                        "confianza": 0.97,
                        "nombre_largo": "Certificado de avalúo fiscal",
                        "razones": "Encabezado SII y campos de rol/avalúo característicos",
                    },
                },
            ],
        },
        {
            "text": (
                "Conservador de Bienes Raíces de Santiago\n"
                "Certificado de Hipotecas, Gravámenes, Prohibiciones e Interdicciones\n"
                "Inmueble: Foja 234 Nº 567 año 2018\n"
                "Se certifica que el inmueble registra una hipoteca a favor de Banco Bice...\n"
            ),
            "extractions": [
                {
                    "extraction_class": "tipo_documento",
                    "extraction_text": "cert_hipotecas_gravamenes",
                    "attributes": {
                        "confianza": 0.95,
                        "nombre_largo": "Certificado de hipotecas y gravámenes",
                        "razones": "Mención explícita del CBR y del título del certificado",
                    },
                },
            ],
        },
        {
            "text": (
                "ESCRITURA PÚBLICA DE COMPRAVENTA\n"
                "En Santiago de Chile, a treinta días del mes de marzo de dos mil veintidós, "
                "ante mí, JUAN PÉREZ GONZÁLEZ, Notario Público, comparecen don..."
            ),
            "extractions": [
                {
                    "extraction_class": "tipo_documento",
                    "extraction_text": "escritura",
                    "attributes": {
                        "confianza": 0.98,
                        "nombre_largo": "Escritura pública",
                        "razones": "Encabezado 'ESCRITURA PÚBLICA' + comparecencia ante notario",
                    },
                },
            ],
        },
    ]


class ClassifierService:
    """Encapsula la llamada a langextract + parseo a ClasificacionResultado."""

    def __init__(self, api_key: str | None = None, model_id: str | None = None):
        self.api_key = api_key or settings.GEMINI_API_KEY
        self.model_id = model_id or settings.GEMINI_MODEL_ID

    def classify(self, text: str) -> ClasificacionResultado:
        """Clasifica un texto usando langextract → Gemini."""
        if not self.api_key:
            logger.warning("GEMINI_API_KEY no configurada. Devolviendo clasificación heurística.")
            return self._fallback_heuristic(text)

        try:
            import langextract as lx  # noqa: WPS433
        except ImportError as exc:
            raise RuntimeError("langextract no instalado. pip install langextract") from exc

        prompt = _build_prompt()
        examples_raw = _build_examples()

        # langextract espera ExampleData; intentamos importar desde lx.data si existe
        try:
            from langextract import data as lxdata  # type: ignore
            examples = [
                lxdata.ExampleData(
                    text=ex["text"],
                    extractions=[
                        lxdata.Extraction(
                            extraction_class=e["extraction_class"],
                            extraction_text=e["extraction_text"],
                            attributes=e.get("attributes", {}),
                        )
                        for e in ex["extractions"]
                    ],
                )
                for ex in examples_raw
            ]
        except ImportError:
            # Fallback: pasar como dicts
            examples = examples_raw

        result = lx.extract(
            text_or_documents=text[:50_000],  # cap por costo/latencia
            prompt_description=prompt,
            examples=examples,
            model_id=self.model_id,
            api_key=self.api_key,
            temperature=settings.LANGEXTRACT_TEMPERATURE,
        )

        return self._parse_result(result)

    def _parse_result(self, lx_result: Any) -> ClasificacionResultado:
        """Convierte la salida de langextract a ClasificacionResultado."""
        extractions = getattr(lx_result, "extractions", []) or []

        tipo: TipoDocumento = "otro"
        confianza = 0.0
        razones: list[str] = []
        spans: list[dict] = []

        for ex in extractions:
            extraction_class = getattr(ex, "extraction_class", None) or (ex.get("extraction_class") if isinstance(ex, dict) else None)
            if extraction_class != "tipo_documento":
                continue

            ex_text = getattr(ex, "extraction_text", None) or (ex.get("extraction_text") if isinstance(ex, dict) else None)
            if ex_text in TIPOS_CATALOGO:
                tipo = ex_text  # type: ignore[assignment]

            attrs = getattr(ex, "attributes", {}) or (ex.get("attributes", {}) if isinstance(ex, dict) else {})
            if "confianza" in attrs:
                try:
                    confianza = float(attrs["confianza"])
                except (TypeError, ValueError):
                    confianza = 0.6
            if "razones" in attrs:
                razones.append(str(attrs["razones"]))

            char_interval = getattr(ex, "char_interval", None)
            if char_interval:
                spans.append({
                    "start_char": getattr(char_interval, "start_pos", None),
                    "end_char": getattr(char_interval, "end_pos", None),
                    "extraction": ex_text,
                })
            break  # nos quedamos con la primera extracción de tipo_documento

        nombre_largo = TIPOS_CATALOGO.get(tipo, {"nombre_largo": "Otro / sin clasificar"})["nombre_largo"]

        return ClasificacionResultado(
            tipo=tipo,
            confianza=round(confianza, 4),
            nombre_largo=nombre_largo,
            razones=razones,
            spans_evidencia=spans,
            requiere_revision=confianza < 0.6 or tipo == "otro",
        )

    def _fallback_heuristic(self, text: str) -> ClasificacionResultado:
        """
        Clasificador de respaldo basado en palabras clave (sin LLM).
        Útil para v0 sin GEMINI_API_KEY o como cache simple.
        """
        t = text.lower()

        rules: list[tuple[TipoDocumento, list[str]]] = [
            ("cert_avaluo_sii",          ["servicio de impuestos internos", "avalúo fiscal", "rol de avalúo"]),
            ("cert_hipotecas_gravamenes",["hipotecas", "gravámenes", "prohibiciones", "interdicciones"]),
            ("cert_dominio_vigente",     ["dominio vigente", "conservador de bienes raíces"]),
            ("cert_municipal",           ["dirección de obras municipales", "certificado de número", "recepción final"]),
            ("escritura",                ["escritura pública", "ante mí", "notario público", "repertorio"]),
            ("plano_propiedad",          ["plano", "deslindes", "cotas", "superficie", "loteo"]),
            ("plan_regulador",           ["plan regulador", "zonificación", "instrumento de planificación"]),
        ]

        for tipo, keywords in rules:
            if sum(1 for kw in keywords if kw in t) >= 2:
                return ClasificacionResultado(
                    tipo=tipo,
                    confianza=0.55,
                    nombre_largo=TIPOS_CATALOGO[tipo]["nombre_largo"],
                    razones=[f"Heurística keyword-based ({len([k for k in keywords if k in t])}/{len(keywords)} matches)"],
                    spans_evidencia=[],
                    requiere_revision=True,
                )

        return ClasificacionResultado(
            tipo="otro",
            confianza=0.2,
            nombre_largo="Otro / sin clasificar",
            razones=["No coincide con ninguna heurística conocida"],
            spans_evidencia=[],
            requiere_revision=True,
        )
