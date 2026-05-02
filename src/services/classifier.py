"""
classifier · clasificación de documentos hipotecarios chilenos vía langextract.

Usa langextract.extract con un prompt few-shot para que Gemini devuelva
estructuradamente:
  - tipo (uno de los códigos del catálogo en src/models/catalogo.py)
  - confianza (0-1)
  - razones (texto)
  - spans_evidencia (fragmentos del texto fuente)

langextract garantiza que cada extracción esté anclada al texto original,
lo que el frontend usa para el resaltado y la auditoría legal.
"""
import logging
from typing import Any

from src.core.config import settings
from src.models.catalogo import CATALOGO, CATALOGO_BY_CODIGO, CODIGOS_VALIDOS
from src.models.schemas import ClasificacionResultado, TipoDocumento
from src.services.triggers import evaluar_triggers

logger = logging.getLogger(__name__)


def _build_prompt() -> str:
    lineas = [
        "Eres un asistente experto en derecho de propiedad raíz chileno.",
        "Clasifica el siguiente documento en EXACTAMENTE una de estas categorías "
        "(devuelve el `codigo` exacto, no el nombre largo):",
        "",
    ]
    for t in CATALOGO:
        if t["codigo"] == "otro":
            continue
        lineas.append(f"- {t['codigo']}: {t['pista']} (emisor: {t['emisor']})")
    lineas.append("- otro: si no encaja en ninguna categoría anterior.")
    lineas.extend([
        "",
        "Devuelve la clasificación junto con fragmentos textuales del documento "
        "que justifiquen tu decisión (autoridad emisora, encabezado, sellos, etc.).",
        "Si dudas entre dos categorías cercanas (ej. cert_matrimonio vs cert_union_civil), "
        "elige según el TÍTULO LITERAL del certificado.",
    ])
    return "\n".join(lineas)


def _build_examples() -> list[dict[str, Any]]:
    """Few-shot examples para langextract. Documentos cortos representativos."""
    return [
        {
            "text": (
                "REPÚBLICA DE CHILE\nSERVICIO DE IMPUESTOS INTERNOS\n"
                "Certificado de Avalúo Fiscal\nRol de avalúo: 12345-7\n"
                "Comuna: Las Condes\nAvalúo total: $ 145.230.000\n"
            ),
            "extractions": [{
                "extraction_class": "tipo_documento",
                "extraction_text": "cert_avaluo_sii",
                "attributes": {
                    "confianza": 0.97,
                    "nombre_largo": "Certificado de avalúo fiscal",
                    "razones": "Encabezado SII y campos de rol/avalúo característicos",
                },
            }],
        },
        {
            "text": (
                "Conservador de Bienes Raíces de Santiago\n"
                "Certificado de Hipotecas, Gravámenes, Prohibiciones e Interdicciones\n"
                "Inmueble: Foja 234 Nº 567 año 2018\n"
                "Se certifica que el inmueble registra una hipoteca a favor de Banco Bice...\n"
            ),
            "extractions": [{
                "extraction_class": "tipo_documento",
                "extraction_text": "cert_hipotecas_gravamenes",
                "attributes": {
                    "confianza": 0.95,
                    "nombre_largo": "Certificado de hipotecas y gravámenes",
                    "razones": "Mención explícita del CBR y del título del certificado",
                },
            }],
        },
        {
            "text": (
                "ESCRITURA PÚBLICA DE COMPRAVENTA\n"
                "En Santiago de Chile, a treinta días del mes de marzo de dos mil veintidós, "
                "ante mí, JUAN PÉREZ GONZÁLEZ, Notario Público, comparecen don..."
            ),
            "extractions": [{
                "extraction_class": "tipo_documento",
                "extraction_text": "escritura_compraventa",
                "attributes": {
                    "confianza": 0.96,
                    "nombre_largo": "Escritura pública de compraventa",
                    "razones": "Encabezado 'ESCRITURA PÚBLICA DE COMPRAVENTA' + comparecencia ante notario",
                },
            }],
        },
        {
            "text": (
                "REPÚBLICA DE CHILE\nSERVICIO DE REGISTRO CIVIL E IDENTIFICACIÓN\n"
                "CERTIFICADO DE MATRIMONIO\n"
                "Cónyuges: Pedro Soto Rojas y María Pérez Vega\n"
                "Régimen: Sociedad Conyugal\n"
            ),
            "extractions": [{
                "extraction_class": "tipo_documento",
                "extraction_text": "cert_matrimonio",
                "attributes": {
                    "confianza": 0.97,
                    "nombre_largo": "Certificado de matrimonio",
                    "razones": "Encabezado del Registro Civil + título 'CERTIFICADO DE MATRIMONIO'",
                },
            }],
        },
        {
            "text": (
                "TESORERÍA GENERAL DE LA REPÚBLICA\n"
                "Certificado de Deuda de Contribuciones\n"
                "Rol: 12345-7  Comuna: Las Condes\n"
                "NO REGISTRA DEUDA pendiente.\n"
            ),
            "extractions": [{
                "extraction_class": "tipo_documento",
                "extraction_text": "cert_deuda_contribuciones",
                "attributes": {
                    "confianza": 0.96,
                    "nombre_largo": "Certificado de deuda de contribuciones",
                    "razones": "Encabezado TGR + título y rol/comuna característicos",
                },
            }],
        },
        {
            "text": (
                "MUNICIPALIDAD DE PROVIDENCIA\nDIRECCIÓN DE OBRAS MUNICIPALES\n"
                "CERTIFICADO DE RECEPCIÓN FINAL\n"
                "Permiso de Edificación N° 234/2018\n"
                "Recepción: TOTAL\n"
            ),
            "extractions": [{
                "extraction_class": "tipo_documento",
                "extraction_text": "cert_recepcion_final_dom",
                "attributes": {
                    "confianza": 0.95,
                    "nombre_largo": "Certificado de recepción final (DOM)",
                    "razones": "Encabezado DOM + título 'RECEPCIÓN FINAL'",
                },
            }],
        },
    ]


class ClassifierService:
    """Encapsula la llamada a langextract + parseo a ClasificacionResultado."""

    def __init__(self, api_key: str | None = None, model_id: str | None = None):
        self.api_key = api_key or settings.GEMINI_API_KEY
        self.model_id = model_id or settings.GEMINI_MODEL_ID
        # Vertex AI usa ADC del SA, no requiere api_key.
        self.use_vertex = settings.VERTEX_AI and bool(settings.GOOGLE_CLOUD_PROJECT)

    def classify(self, text: str) -> ClasificacionResultado:
        """Clasifica un texto usando langextract → Gemini, y evalúa triggers IF/THEN."""
        if not self.use_vertex and not self.api_key:
            logger.warning("Sin VERTEX_AI ni GEMINI_API_KEY. Usando heurística.")
            base = self._fallback_heuristic(text)
        else:
            base = self._classify_via_langextract(text)

        # Evaluar triggers — independiente del clasificador. Esto detecta
        # condiciones como bien familiar, usufructo, condominio, etc.
        triggers_gatillados = evaluar_triggers(text=text, tipo=base.tipo)
        base.triggers = [t["trigger_id"] for t in triggers_gatillados]
        return base

    def _classify_via_langextract(self, text: str) -> ClasificacionResultado:
        try:
            import langextract as lx  # noqa: WPS433
        except ImportError as exc:
            raise RuntimeError("langextract no instalado. pip install langextract") from exc

        prompt = _build_prompt()
        examples_raw = _build_examples()

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
            examples = examples_raw

        if self.use_vertex:
            extract_kwargs = {
                "language_model_params": {
                    "vertexai": True,
                    "project": settings.GOOGLE_CLOUD_PROJECT,
                    "location": settings.VERTEX_LOCATION,
                },
            }
        else:
            extract_kwargs = {"api_key": self.api_key}

        result = lx.extract(
            text_or_documents=text[:50_000],
            prompt_description=prompt,
            examples=examples,
            model_id=self.model_id,
            temperature=settings.LANGEXTRACT_TEMPERATURE,
            **extract_kwargs,
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
            extraction_class = getattr(ex, "extraction_class", None) or (
                ex.get("extraction_class") if isinstance(ex, dict) else None
            )
            if extraction_class != "tipo_documento":
                continue

            ex_text = getattr(ex, "extraction_text", None) or (
                ex.get("extraction_text") if isinstance(ex, dict) else None
            )
            if ex_text in CODIGOS_VALIDOS:
                tipo = ex_text

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
            break

        nombre_largo = CATALOGO_BY_CODIGO.get(tipo, {"nombre_largo": "Otro / sin clasificar"})["nombre_largo"]

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

        rules: list[tuple[str, list[str]]] = [
            ("cert_avaluo_sii",          ["servicio de impuestos internos", "avalúo fiscal", "rol de avalúo"]),
            ("cert_hipotecas_gravamenes",["hipotecas", "gravámenes", "prohibiciones", "interdicciones"]),
            ("cert_dominio_vigente",     ["dominio vigente", "conservador de bienes raíces"]),
            ("cert_recepcion_final_dom", ["recepción final", "dirección de obras municipales"]),
            ("cert_numero_dom",          ["certificado de número", "dirección de obras"]),
            ("cert_no_expropiacion_dom", ["no expropiación", "afectación a utilidad pública"]),
            ("cert_deuda_contribuciones",["tesorería general", "deuda de contribuciones", "no registra deuda"]),
            ("cert_matrimonio",          ["servicio de registro civil", "certificado de matrimonio", "cónyuges"]),
            ("cert_union_civil",         ["acuerdo de unión civil", "convivientes civiles"]),
            ("cert_solteria",            ["certificado de soltería", "estado civil soltero"]),
            ("cert_defuncion",           ["certificado de defunción", "fecha de defunción"]),
            ("decl_jurada_solteria",     ["declaración jurada", "soltería", "firma"]),
            ("carnet_identidad",         ["cédula de identidad", "rut", "fecha de nacimiento"]),
            ("escritura_compraventa",    ["escritura pública", "compraventa", "ante mí", "notario público"]),
            ("escritura_alzamiento_hipoteca", ["alzamiento", "hipoteca", "cancela"]),
            ("e_rut_sii",                ["e-rut", "rut electrónico", "servicio de impuestos internos"]),
            ("escritura_constitucion_social", ["constitución de sociedad", "razón social", "estatutos"]),
            ("cert_vigencia_poderes",    ["vigencia de poderes", "registro de comercio", "tu empresa en un día"]),
            ("cert_deuda_gastos_comunes",["gastos comunes", "comunidad", "administración"]),
            ("acta_asamblea_copropietarios", ["asamblea de copropietarios", "comité de administración"]),
            ("resolucion_serviu",        ["serviu", "resolución", "subsidio"]),
            ("sentencia_judicial",       ["tribunal de familia", "sentencia", "resuélvese"]),
            ("cert_ejecutoria",          ["certificado de ejecutoria", "ejecutoriada"]),
            ("cert_subdivision_sag",     ["servicio agrícola y ganadero", "subdivisión predial", "d.l. 3.516"]),
            ("plano_subdivision_sag",    ["plano", "subdivisión", "sag", "archivado"]),
            ("cert_conadi",              ["conadi", "calidad indígena", "ley 19.253"]),
            ("cert_posesion_efectiva",   ["posesión efectiva", "auto", "herederos"]),
            ("cert_exencion_herencia_sii", ["impuesto a las herencias", "exención"]),
            ("plano_propiedad",          ["plano", "deslindes", "cotas", "superficie"]),
            ("plan_regulador",           ["plan regulador", "zonificación", "instrumento de planificación"]),
        ]

        best_tipo = "otro"
        best_score = 0
        for tipo, keywords in rules:
            score = sum(1 for kw in keywords if kw in t)
            if score >= 2 and score > best_score:
                best_tipo = tipo
                best_score = score

        if best_tipo == "otro":
            return ClasificacionResultado(
                tipo="otro",
                confianza=0.0,
                nombre_largo="Otro / sin clasificar",
                razones=["No se identificó por keywords"],
                spans_evidencia=[],
                requiere_revision=True,
            )

        nombre_largo = CATALOGO_BY_CODIGO[best_tipo]["nombre_largo"]
        return ClasificacionResultado(
            tipo=best_tipo,
            confianza=min(0.55 + 0.05 * best_score, 0.85),
            nombre_largo=nombre_largo,
            razones=[f"Heurística keyword-based ({best_score} matches)"],
            spans_evidencia=[],
            requiere_revision=True,
        )
