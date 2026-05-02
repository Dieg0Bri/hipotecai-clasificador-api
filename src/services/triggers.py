"""
triggers · reglas IF/THEN que detectan condiciones especiales en un documento
y solicitan documentos adicionales al cliente.

Spec del abogado (Doc 3 - SISTEMA DE ALERTAS Y GATILLOS):
  1. Condominio       → pide gastos comunes + acta asamblea
  2. Subsidio SERVIU  → pide resolución SERVIU
  3. Menor de edad    → pide sentencia tribunal + ejecutoria  (DEFERIDO: requiere extracción)
  4. Rural / Parcela  → pide cert subdivisión SAG + plano SAG
  5. Casa con metraje → pide recepción final DOM              (DEFERIDO: requiere cruce de datos)
  6. Bien Familiar    → pide escritura cónyuge / desafectación
  7. Usufructo        → pide renuncia / certificado defunción
  8. Ley Indígena     → pide certificado CONADI

Cada trigger es una función pura (text, tipo) -> dict | None que devuelve
una "solicitud" si se gatilla. La solicitud es portable: el clasificador
puede persistirla a BBDD o publicarla por Pub/Sub más adelante.
"""
from __future__ import annotations

import logging
import re
from typing import Callable, Optional, TypedDict

logger = logging.getLogger(__name__)


class SolicitudDocumento(TypedDict):
    trigger_id: str          # ID estable de la regla (ej. "T-CONDOMINIO")
    motivo: str              # mensaje al letrado/cliente
    codigos_solicitados: list[str]  # códigos del catálogo a pedir
    matched_phrase: str      # fragmento del texto que gatilló
    tipo_documento: str      # tipo del doc actual (para contexto)


# --------------------------------------------------------------------- helpers


def _find_first_match(text: str, patterns: list[str]) -> Optional[str]:
    """Devuelve la primera coincidencia (case-insensitive) de cualquier patrón."""
    t = text
    for pat in patterns:
        m = re.search(pat, t, flags=re.IGNORECASE | re.UNICODE)
        if m:
            return m.group(0)
    return None


# --------------------------------------------------------------------- triggers


def trigger_condominio(text: str, tipo: str) -> Optional[SolicitudDocumento]:
    """T-CONDOMINIO: detecta dpto/condominio en cert. dominio vigente."""
    if tipo not in {"cert_dominio_vigente", "escritura_compraventa", "escritura_anterior"}:
        return None
    patrones = [
        r"\bley\s+de\s+copropiedad\s+inmobiliaria\b",
        r"\bdepartamento\s+n[°º]\b",
        r"\bestacionamiento\s+n[°º]\b",
        r"\bbodega\s+n[°º]\b",
        r"\bcondominio\b",
    ]
    match = _find_first_match(text, patrones)
    if not match:
        return None
    return {
        "trigger_id": "T-CONDOMINIO",
        "motivo": (
            "La propiedad forma parte de un condominio o edificio. La ley exige "
            "comprobar que no hay deudas con la comunidad."
        ),
        "codigos_solicitados": ["cert_deuda_gastos_comunes", "acta_asamblea_copropietarios"],
        "matched_phrase": match,
        "tipo_documento": tipo,
    }


def trigger_subsidio_serviu(text: str, tipo: str) -> Optional[SolicitudDocumento]:
    """T-SUBSIDIO: detecta SERVIU/subsidio en GP."""
    if tipo not in {"cert_hipotecas_gravamenes"}:
        return None
    patrones = [
        r"\bprohibici[oó]n\s+de\s+enajenar\s+a\s+favor\s+del\s+serviu\b",
        r"\bserviu\b.*\bsubsidio\b",
        r"\bsubsidio\s+habitacional\b",
        r"\bprohibici[oó]n.*\bserviu\b",
    ]
    match = _find_first_match(text, patrones)
    if not match:
        return None
    return {
        "trigger_id": "T-SUBSIDIO",
        "motivo": (
            "La propiedad registra una prohibición a favor del SERVIU por subsidio "
            "habitacional. Para venderla hay que liberar la restricción."
        ),
        "codigos_solicitados": ["resolucion_serviu"],
        "matched_phrase": match,
        "tipo_documento": tipo,
    }


def trigger_rural(text: str, tipo: str) -> Optional[SolicitudDocumento]:
    """T-RURAL: detecta predio rústico/parcela/D.L. 3.516."""
    if tipo not in {"cert_avaluo_sii", "cert_dominio_vigente", "escritura_compraventa", "escritura_anterior"}:
        return None
    patrones = [
        r"\bd\.\s*l\.?\s*3[\.\s]*516\b",
        r"\bpredio\s+r[úu]stico\b",
        r"\bparcela\s+de\s+agrado\b",
        r"\bdestino\s*:\s*agr[íi]cola\b",
        r"\bsubdivisi[óo]n\s+predial\b",
    ]
    match = _find_first_match(text, patrones)
    if not match:
        return None
    return {
        "trigger_id": "T-RURAL",
        "motivo": (
            "La propiedad parece rural o parcela de agrado. La normativa exige "
            "verificar la subdivisión aprobada por el SAG."
        ),
        "codigos_solicitados": ["cert_subdivision_sag", "plano_subdivision_sag"],
        "matched_phrase": match,
        "tipo_documento": tipo,
    }


def trigger_bien_familiar(text: str, tipo: str) -> Optional[SolicitudDocumento]:
    """T-BIEN-FAMILIAR: detecta declaración de bien familiar en GP."""
    if tipo not in {"cert_hipotecas_gravamenes", "cert_dominio_vigente"}:
        return None
    patrones = [
        r"\bdeclaraci[óo]n\s+de\s+bien\s+familiar\b",
        r"\bbien\s+familiar\b",
        r"\bart[íi]culo\s+141\b",  # Código Civil
    ]
    match = _find_first_match(text, patrones)
    if not match:
        return None
    return {
        "trigger_id": "T-BIEN-FAMILIAR",
        "motivo": (
            "La propiedad está declarada como Bien Familiar. Se necesita el "
            "consentimiento formal del cónyuge no propietario para vender o hipotecar."
        ),
        "codigos_solicitados": ["escritura_bien_familiar"],
        "matched_phrase": match,
        "tipo_documento": tipo,
    }


def trigger_usufructo(text: str, tipo: str) -> Optional[SolicitudDocumento]:
    """T-USUFRUCTO: detecta usufructo / nuda propiedad / derecho de uso."""
    if tipo not in {"cert_dominio_vigente", "cert_hipotecas_gravamenes", "escritura_compraventa", "escritura_anterior"}:
        return None
    patrones = [
        r"\busufructo\s+vitalicio\b",
        r"\busufructo\b",
        r"\bnuda\s+propiedad\b",
        r"\bderecho\s+de\s+uso\s+y\s+habitaci[óo]n\b",
        r"\bderecho\s+de\s+uso\b",
    ]
    match = _find_first_match(text, patrones)
    if not match:
        return None
    return {
        "trigger_id": "T-USUFRUCTO",
        "motivo": (
            "Existe un usufructo o derecho de uso vigente sobre la propiedad. "
            "Para vender libre de problemas hay que alzarlo."
        ),
        "codigos_solicitados": ["escritura_renuncia_usufructo", "cert_defuncion"],
        "matched_phrase": match,
        "tipo_documento": tipo,
    }


def trigger_indigena(text: str, tipo: str) -> Optional[SolicitudDocumento]:
    """T-INDIGENA: detecta tierras indígenas (Ley 19.253)."""
    patrones = [
        r"\bcalidad\s+ind[íi]gena\b",
        r"\bley\s+19[.\s]*253\b",
        r"\bconadi\b",
        r"\btierra\s+ind[íi]gena\b",
    ]
    match = _find_first_match(text, patrones)
    if not match:
        return None
    return {
        "trigger_id": "T-INDIGENA",
        "motivo": (
            "La propiedad está regulada por la Ley Indígena 19.253. Esto restringe "
            "legalmente a quién se le puede vender — ambas partes deben acreditar calidad indígena."
        ),
        "codigos_solicitados": ["cert_conadi"],
        "matched_phrase": match,
        "tipo_documento": tipo,
    }


def trigger_persona_juridica(text: str, tipo: str) -> Optional[SolicitudDocumento]:
    """T-PJ: detecta vendedor persona jurídica (no en spec original pero es base+)."""
    if tipo not in {"escritura_compraventa", "cert_dominio_vigente"}:
        return None
    patrones = [
        r"\bsociedad\s+por\s+acciones\b",
        r"\bsociedad\s+an[óo]nima\b",
        r"\blimitada\b",
        r"\bspa\b",
        r"\bs\.a\.\b",
        r"\bs\.p\.a\.\b",
        r"\brut\s*:?\s*7[6-9][\.\d]+",  # RUT > 76.000.000 típico de empresa
    ]
    match = _find_first_match(text, patrones)
    if not match:
        return None
    return {
        "trigger_id": "T-PERSONA-JURIDICA",
        "motivo": (
            "El vendedor parece ser persona jurídica (empresa). Se requieren los "
            "antecedentes societarios para acreditar facultades de los firmantes."
        ),
        "codigos_solicitados": ["e_rut_sii", "escritura_constitucion_social", "cert_vigencia_poderes"],
        "matched_phrase": match,
        "tipo_documento": tipo,
    }


def trigger_alzamiento_hipoteca(text: str, tipo: str) -> Optional[SolicitudDocumento]:
    """T-ALZAMIENTO: detecta hipoteca activa de banco (no SERVIU) en GP."""
    if tipo not in {"cert_hipotecas_gravamenes"}:
        return None
    if not _find_first_match(text, [r"\bhipoteca\b"]):
        return None
    # Si menciona SERVIU, ya lo cubre T-SUBSIDIO; aquí solo si es banco privado.
    if _find_first_match(text, [r"\bserviu\b"]):
        return None
    patrones = [
        r"\bbanco\s+(estado|santander|bci|chile|scotiabank|itau|security|falabella|bice|consorcio|internacional)\b",
        r"\ba\s+favor\s+de\s+banco\b",
    ]
    match = _find_first_match(text, patrones)
    if not match:
        return None
    return {
        "trigger_id": "T-ALZAMIENTO",
        "motivo": (
            "El certificado de hipotecas y gravámenes registra una hipoteca a favor "
            "de un banco. Para la venta hay que acompañar la escritura de alzamiento."
        ),
        "codigos_solicitados": ["escritura_alzamiento_hipoteca"],
        "matched_phrase": match,
        "tipo_documento": tipo,
    }


# --------------------------------------------------------------------- registry

ALL_TRIGGERS: list[Callable[[str, str], Optional[SolicitudDocumento]]] = [
    trigger_condominio,
    trigger_subsidio_serviu,
    trigger_rural,
    trigger_bien_familiar,
    trigger_usufructo,
    trigger_indigena,
    trigger_persona_juridica,
    trigger_alzamiento_hipoteca,
]


def evaluar_triggers(text: str, tipo: str) -> list[SolicitudDocumento]:
    """Aplica todas las reglas y devuelve las solicitudes gatilladas."""
    out: list[SolicitudDocumento] = []
    for trig in ALL_TRIGGERS:
        try:
            r = trig(text, tipo)
            if r:
                out.append(r)
        except Exception as exc:  # noqa: BLE001
            logger.exception("trigger %s falló", trig.__name__)
    return out
