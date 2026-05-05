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


def _build_prompt(page_stats: dict | None = None) -> str:
    lineas = [
        "Eres un asistente experto en derecho de propiedad raíz chileno.",
        "Clasifica el documento extrayendo:",
        "  - extraction_class: 'tipo_documento'",
        "  - extraction_text: el FRAGMENTO LITERAL del documento que delata el tipo "
        "(típicamente el título o el encabezado del organismo emisor — copialo exacto del texto).",
        "  - attributes.codigo: el código exacto de una de estas categorías:",
        "",
    ]
    for t in CATALOGO:
        if t["codigo"] == "otro":
            continue
        lineas.append(f"      · {t['codigo']}: {t['pista']} (emisor: {t['emisor']})")
    lineas.append("      · otro: si no encaja en ninguna categoría anterior.")
    lineas.extend([
        "",
        "  - attributes.confianza: número 0.0-1.0",
        "  - attributes.razones: por qué elegiste ese código (1 oración)",
        "",
        "IMPORTANTE: extraction_text DEBE ser un substring exacto del documento, no el código.",
        "Si dudas entre dos categorías cercanas (ej. cert_matrimonio vs cert_union_civil), "
        "elige según el TÍTULO LITERAL del certificado.",
    ])

    # Decisión de OCR (también en el output del clasificador). Se incluye sólo
    # cuando hay datos por página — modo /classify-text plano la omite.
    if page_stats:
        lineas.extend([
            "",
            "ADEMÁS, decide si el documento necesita OCR:",
            "  - attributes.requiere_ocr: true | false",
            "  - attributes.razon_ocr: 1 oración explicando por qué (ej. 'cuerpo escaneado "
            "desde la página 2', 'documento puramente digital, no necesita OCR').",
            "",
            "Datos del extractor de texto nativo (pdfplumber):",
            f"  · Total de páginas:           {page_stats['n_total']}",
            f"  · Páginas con texto seleccionable: {page_stats['paginas_con_texto']}",
            f"  · Páginas vacías o casi vacías:    {page_stats['paginas_vacias']}",
            f"  · Caracteres alfanuméricos por página: {page_stats['chars_por_pagina']}",
            "",
            "Heurística para tu decisión (no rígida — usa criterio):",
            "  - Si la mayoría de páginas vienen vacías y este es un documento de "
            "varias páginas (ej. una escritura), TÍPICAMENTE es escaneado y necesita OCR.",
            "  - Carátula notarial digital (página 1 con pocas líneas) + cuerpo "
            "vacío de pdfplumber = ESCANEADO, requiere OCR.",
            "  - Documento de 1 página con texto extraíble = NO necesita OCR.",
        ])
    return "\n".join(lineas)


def _build_examples() -> list[dict[str, Any]]:
    """Few-shot examples para langextract.

    extraction_text DEBE ser un substring literal del `text` del example —
    langextract lo alinea contra el documento original. El código del catálogo
    va en `attributes.codigo`.
    """
    return [
        {
            "text": (
                "REPÚBLICA DE CHILE\nSERVICIO DE IMPUESTOS INTERNOS\n"
                "Certificado de Avalúo Fiscal\nRol de avalúo: 12345-7\n"
                "Comuna: Las Condes\nAvalúo total: $ 145.230.000\n"
            ),
            "extractions": [{
                "extraction_class": "tipo_documento",
                "extraction_text": "Certificado de Avalúo Fiscal",
                "attributes": {
                    "codigo": "cert_avaluo_sii",
                    "confianza": 0.97,
                    "razones": "Encabezado SII y título 'Certificado de Avalúo Fiscal'",
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
                "extraction_text": "Certificado de Hipotecas, Gravámenes, Prohibiciones e Interdicciones",
                "attributes": {
                    "codigo": "cert_hipotecas_gravamenes",
                    "confianza": 0.95,
                    "razones": "CBR + título completo del certificado de hipotecas y gravámenes",
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
                "extraction_text": "ESCRITURA PÚBLICA DE COMPRAVENTA",
                "attributes": {
                    "codigo": "escritura_compraventa",
                    "confianza": 0.96,
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
                "extraction_text": "CERTIFICADO DE MATRIMONIO",
                "attributes": {
                    "codigo": "cert_matrimonio",
                    "confianza": 0.97,
                    "razones": "Registro Civil + título 'CERTIFICADO DE MATRIMONIO'",
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
                "extraction_text": "Certificado de Deuda de Contribuciones",
                "attributes": {
                    "codigo": "cert_deuda_contribuciones",
                    "confianza": 0.96,
                    "razones": "TGR + título 'Certificado de Deuda de Contribuciones'",
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
                "extraction_text": "CERTIFICADO DE RECEPCIÓN FINAL",
                "attributes": {
                    "codigo": "cert_recepcion_final_dom",
                    "confianza": 0.95,
                    "razones": "DOM + título 'CERTIFICADO DE RECEPCIÓN FINAL'",
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

    def classify(self, text: str, page_stats: dict | None = None) -> ClasificacionResultado:
        """Clasifica un texto usando langextract → Gemini, y evalúa triggers IF/THEN.

        Si `page_stats` viene poblado (caso de un PDF real con stats por
        página), Gemini también decide si el documento necesita OCR. La
        decisión queda en `result.requiere_ocr` + `result.razon_ocr`.
        """
        # Caso especial: 0 páginas con texto = escaneo puro. No vale la pena
        # llamar a Gemini sin texto — saltamos directo a "necesita OCR" y la
        # clasificación real se re-corre después con el texto OCR.
        if page_stats and page_stats.get("n_con_texto", 0) == 0:
            return ClasificacionResultado(
                tipo="otro",
                confianza=0.0,
                nombre_largo="Pendiente de OCR",
                razones=["Documento sin texto seleccionable — clasificación se hará tras OCR."],
                spans_evidencia=[],
                requiere_revision=True,
                requiere_ocr=True,
                razon_ocr=f"Las {page_stats['n_total']} páginas vienen sin texto extraíble (escaneo).",
            )

        if not self.use_vertex and not self.api_key:
            logger.warning("Sin VERTEX_AI ni GEMINI_API_KEY. Usando heurística.")
            base = self._fallback_heuristic(text)
            # Heurística simple para OCR sin Gemini: si más del 50% páginas vacías → OCR.
            if page_stats and page_stats["n_vacias"] > page_stats["n_con_texto"]:
                base.requiere_ocr = True
                base.razon_ocr = (
                    f"Heurística: {page_stats['n_vacias']}/{page_stats['n_total']} "
                    "páginas vacías sugiere documento escaneado."
                )
        else:
            base = self._classify_via_langextract(text, page_stats=page_stats)

        # Evaluar triggers — independiente del clasificador. Esto detecta
        # condiciones como bien familiar, usufructo, condominio, etc.
        triggers_gatillados = evaluar_triggers(text=text, tipo=base.tipo)
        base.triggers = [t["trigger_id"] for t in triggers_gatillados]
        return base

    def _classify_via_langextract(self, text: str, page_stats: dict | None = None) -> ClasificacionResultado:
        try:
            import langextract as lx  # noqa: WPS433
        except ImportError as exc:
            raise RuntimeError("langextract no instalado. pip install langextract") from exc

        prompt = _build_prompt(page_stats=page_stats)
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
        requiere_ocr_attr: bool | None = None
        razon_ocr_attr: str | None = None

        for ex in extractions:
            extraction_class = getattr(ex, "extraction_class", None) or (
                ex.get("extraction_class") if isinstance(ex, dict) else None
            )
            if extraction_class != "tipo_documento":
                continue

            ex_text = getattr(ex, "extraction_text", None) or (
                ex.get("extraction_text") if isinstance(ex, dict) else None
            )
            attrs = getattr(ex, "attributes", {}) or (ex.get("attributes", {}) if isinstance(ex, dict) else {})

            # El código del catálogo viene en attributes.codigo; si por algún motivo
            # llegó en extraction_text (compat retro), lo aceptamos también.
            codigo_attr = attrs.get("codigo") if attrs else None
            if codigo_attr in CODIGOS_VALIDOS:
                tipo = codigo_attr
            elif ex_text in CODIGOS_VALIDOS:  # retro-compat
                tipo = ex_text

            if "confianza" in attrs:
                try:
                    confianza = float(attrs["confianza"])
                except (TypeError, ValueError):
                    confianza = 0.6
            if "razones" in attrs:
                razones.append(str(attrs["razones"]))

            # Decisión OCR (sólo presente cuando el prompt incluyó page_stats).
            if "requiere_ocr" in attrs:
                req_raw = attrs["requiere_ocr"]
                if isinstance(req_raw, bool):
                    requiere_ocr_attr = req_raw
                else:
                    requiere_ocr_attr = str(req_raw).lower() in ("true", "1", "sí", "si", "yes")
            else:
                requiere_ocr_attr = None
            razon_ocr_attr = attrs.get("razon_ocr") if "razon_ocr" in attrs else None

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
            requiere_ocr=bool(requiere_ocr_attr) if requiere_ocr_attr is not None else False,
            razon_ocr=str(razon_ocr_attr) if razon_ocr_attr else None,
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
