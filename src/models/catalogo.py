"""
Catálogo de tipos de documento del estudio de títulos chileno.
--------------------------------------------------------------
Esta es la fuente de verdad. Otros servicios (documentos-api,
estudios-service, frontend) replican o consumen este catálogo.

Estructura:
  - codigo:       slug ASCII estable (no cambiar; es FK lógica en BBDD)
  - nombre_largo: nombre humano (UI)
  - categoria:    grupo legal (identidad, estado_civil, cbr, sii, ...)
  - tipo:         "base" (siempre se pide) | "condicional" (se pide al
                  detectar una condición vía trigger)
  - emisor:       quién lo emite (Conservador, SII, DOM, etc.)
  - pista:        hint para el clasificador LLM
  - vigencia_dias: caducidad legal en días, None si no aplica
"""
from __future__ import annotations

from typing import Literal, TypedDict


class TipoDocCatalogo(TypedDict):
    codigo: str
    nombre_largo: str
    categoria: str
    tipo: Literal["base", "condicional"]
    emisor: str
    pista: str
    vigencia_dias: int | None


CATALOGO: list[TipoDocCatalogo] = [
    # ---------- IDENTIDAD ----------
    {
        "codigo": "carnet_identidad",
        "nombre_largo": "Carnet de Identidad",
        "categoria": "identidad",
        "tipo": "base",
        "emisor": "Registro Civil",
        "pista": "cédula de identidad chilena con RUT, foto, fecha de nacimiento, estado civil declarado",
        "vigencia_dias": None,
    },
    # ---------- ESTADO CIVIL ----------
    {
        "codigo": "cert_matrimonio",
        "nombre_largo": "Certificado de Matrimonio",
        "categoria": "estado_civil",
        "tipo": "base",
        "emisor": "Registro Civil",
        "pista": "certificado del Registro Civil con cónyuges, régimen matrimonial, subinscripciones (separación de bienes, divorcio)",
        "vigencia_dias": None,
    },
    {
        "codigo": "cert_union_civil",
        "nombre_largo": "Certificado de Unión Civil",
        "categoria": "estado_civil",
        "tipo": "base",
        "emisor": "Registro Civil",
        "pista": "certificado de acuerdo de unión civil (AUC) similar al de matrimonio pero con título distinto",
        "vigencia_dias": None,
    },
    {
        "codigo": "cert_solteria",
        "nombre_largo": "Certificado de Soltería",
        "categoria": "estado_civil",
        "tipo": "base",
        "emisor": "Registro Civil",
        "pista": "certificado oficial del Registro Civil que acredita estado civil soltero",
        "vigencia_dias": None,
    },
    {
        "codigo": "decl_jurada_solteria",
        "nombre_largo": "Declaración Jurada de Soltería",
        "categoria": "estado_civil",
        "tipo": "base",
        "emisor": "Particular",
        "pista": "declaración personal simple, en Word o impresa, con firma manuscrita escaneada (NO notarial)",
        "vigencia_dias": None,
    },
    {
        "codigo": "cert_defuncion",
        "nombre_largo": "Certificado de Defunción",
        "categoria": "estado_civil",
        "tipo": "condicional",
        "emisor": "Registro Civil",
        "pista": "certificado de defunción del Registro Civil (vital para viudez o cancelación de usufructo vitalicio)",
        "vigencia_dias": None,
    },
    # ---------- CBR (Conservador de Bienes Raíces) ----------
    {
        "codigo": "cert_dominio_vigente",
        "nombre_largo": "Certificado de Dominio Vigente",
        "categoria": "cbr",
        "tipo": "base",
        "emisor": "Conservador de Bienes Raíces",
        "pista": "emitido por el CBR, indica titular actual con foja, número y año de inscripción",
        "vigencia_dias": 60,
    },
    {
        "codigo": "cert_hipotecas_gravamenes",
        "nombre_largo": "Certificado de Hipotecas y Gravámenes",
        "categoria": "cbr",
        "tipo": "base",
        "emisor": "Conservador de Bienes Raíces",
        "pista": "emitido por el CBR, lista hipotecas, prohibiciones, embargos, bien familiar; también llamado 'GP'",
        "vigencia_dias": 60,
    },
    {
        "codigo": "escritura_compraventa",
        "nombre_largo": "Escritura de Compraventa",
        "categoria": "escrituras",
        "tipo": "base",
        "emisor": "Notaría",
        "pista": "escritura pública de compraventa firmada ante notario, con comparecientes, deslindes y precio",
        "vigencia_dias": None,
    },
    {
        "codigo": "escritura_anterior",
        "nombre_largo": "Escritura Anterior (Tracto 10 años)",
        "categoria": "escrituras",
        "tipo": "base",
        "emisor": "Notaría",
        "pista": "copia de escritura previa (compraventa, donación, adjudicación) que parte del tracto sucesivo de 10 años",
        "vigencia_dias": None,
    },
    {
        "codigo": "escritura_alzamiento_hipoteca",
        "nombre_largo": "Escritura de Alzamiento Hipotecario",
        "categoria": "escrituras",
        "tipo": "condicional",
        "emisor": "Notaría",
        "pista": "escritura donde un banco (BancoEstado, Santander, etc.) alza y cancela una hipoteca",
        "vigencia_dias": None,
    },
    {
        "codigo": "escritura_bien_familiar",
        "nombre_largo": "Escritura sobre Bien Familiar",
        "categoria": "escrituras",
        "tipo": "condicional",
        "emisor": "Notaría",
        "pista": "escritura donde el cónyuge no propietario autoriza venta/hipoteca, o desafectación de bien familiar",
        "vigencia_dias": None,
    },
    {
        "codigo": "escritura_renuncia_usufructo",
        "nombre_largo": "Escritura de Renuncia de Usufructo",
        "categoria": "escrituras",
        "tipo": "condicional",
        "emisor": "Notaría",
        "pista": "escritura pública donde el usufructuario renuncia y alza el usufructo",
        "vigencia_dias": None,
    },
    # ---------- TGR / SII (tributario) ----------
    {
        "codigo": "cert_deuda_contribuciones",
        "nombre_largo": "Certificado de Deuda de Contribuciones",
        "categoria": "tributario",
        "tipo": "base",
        "emisor": "Tesorería General de la República (TGR)",
        "pista": "certificado de la TGR sobre deuda de contribuciones de bienes raíces (al día / morosa)",
        "vigencia_dias": 60,
    },
    {
        "codigo": "cert_avaluo_sii",
        "nombre_largo": "Certificado de Avalúo Fiscal",
        "categoria": "tributario",
        "tipo": "base",
        "emisor": "Servicio de Impuestos Internos (SII)",
        "pista": "certificado del SII con rol de avalúo, comuna, avalúo total, condición exento/afecto",
        "vigencia_dias": 180,
    },
    # ---------- DOM / SERVIU (urbanismo) ----------
    {
        "codigo": "cert_no_expropiacion_dom",
        "nombre_largo": "Certificado de No Expropiación (DOM)",
        "categoria": "urbanismo",
        "tipo": "base",
        "emisor": "Dirección de Obras Municipales (DOM)",
        "pista": "certificado municipal que dice si la propiedad está afecta o no a expropiación municipal",
        "vigencia_dias": 90,
    },
    {
        "codigo": "cert_no_expropiacion_serviu",
        "nombre_largo": "Certificado de No Expropiación (SERVIU)",
        "categoria": "urbanismo",
        "tipo": "base",
        "emisor": "SERVIU",
        "pista": "certificado del SERVIU sobre afectación a utilidad pública (ensanches, obras viales)",
        "vigencia_dias": 90,
    },
    {
        "codigo": "cert_numero_dom",
        "nombre_largo": "Certificado de Número",
        "categoria": "urbanismo",
        "tipo": "base",
        "emisor": "Dirección de Obras Municipales (DOM)",
        "pista": "certificado municipal con la dirección oficial (calle y número) asignada al inmueble",
        "vigencia_dias": 90,
    },
    {
        "codigo": "cert_recepcion_final_dom",
        "nombre_largo": "Certificado de Recepción Final",
        "categoria": "urbanismo",
        "tipo": "base",
        "emisor": "Dirección de Obras Municipales (DOM)",
        "pista": "certificado de recepción final/definitiva de obras emitido por la DOM (estado: total/parcial/sin recepción)",
        "vigencia_dias": None,
    },
    # ---------- PERSONA JURÍDICA (condicional) ----------
    {
        "codigo": "e_rut_sii",
        "nombre_largo": "e-RUT (Persona Jurídica)",
        "categoria": "persona_juridica",
        "tipo": "condicional",
        "emisor": "Servicio de Impuestos Internos (SII)",
        "pista": "PDF emitido por el SII con el RUT de la sociedad y datos del contribuyente",
        "vigencia_dias": None,
    },
    {
        "codigo": "escritura_constitucion_social",
        "nombre_largo": "Escritura de Constitución Social",
        "categoria": "persona_juridica",
        "tipo": "condicional",
        "emisor": "Notaría",
        "pista": "escritura pública de constitución de sociedad con extracto protocolizado",
        "vigencia_dias": None,
    },
    {
        "codigo": "cert_vigencia_poderes",
        "nombre_largo": "Certificado de Vigencia de Poderes",
        "categoria": "persona_juridica",
        "tipo": "condicional",
        "emisor": "Conservador de Comercio / Tu Empresa en un Día",
        "pista": "certificado del Registro de Comercio o portal RES sobre vigencia de poderes de los representantes",
        "vigencia_dias": 30,
    },
    # ---------- CONDOMINIO (condicional) ----------
    {
        "codigo": "cert_deuda_gastos_comunes",
        "nombre_largo": "Certificado de Deuda de Gastos Comunes",
        "categoria": "condominio",
        "tipo": "condicional",
        "emisor": "Administración de la Comunidad",
        "pista": "certificado emitido por la administración del edificio/condominio con deuda de gastos comunes",
        "vigencia_dias": 30,
    },
    {
        "codigo": "acta_asamblea_copropietarios",
        "nombre_largo": "Acta de Asamblea de Copropietarios",
        "categoria": "condominio",
        "tipo": "condicional",
        "emisor": "Comunidad / Notaría",
        "pista": "acta reducida a escritura pública que nombra al administrador o comité de administración",
        "vigencia_dias": None,
    },
    # ---------- SUBSIDIO SERVIU (condicional) ----------
    {
        "codigo": "resolucion_serviu",
        "nombre_largo": "Resolución SERVIU",
        "categoria": "serviu",
        "tipo": "condicional",
        "emisor": "SERVIU",
        "pista": "resolución del SERVIU que autoriza venta anticipada o alza la prohibición de subsidio",
        "vigencia_dias": None,
    },
    # ---------- TRIBUNALES (condicional) ----------
    {
        "codigo": "sentencia_judicial",
        "nombre_largo": "Sentencia Judicial",
        "categoria": "tribunales",
        "tipo": "condicional",
        "emisor": "Tribunal de Familia / Civil",
        "pista": "sentencia del Tribunal de Familia o Civil (ej. autoriza venta de menor); descargada de Oficina Judicial Virtual",
        "vigencia_dias": None,
    },
    {
        "codigo": "cert_ejecutoria",
        "nombre_largo": "Certificado de Ejecutoria",
        "categoria": "tribunales",
        "tipo": "condicional",
        "emisor": "Tribunal",
        "pista": "certificado que acredita que la sentencia está ejecutoriada (firme, no admite apelación); con códigos de barras del Poder Judicial",
        "vigencia_dias": None,
    },
    # ---------- RURAL / SAG (condicional) ----------
    {
        "codigo": "cert_subdivision_sag",
        "nombre_largo": "Certificado de Subdivisión SAG",
        "categoria": "rural",
        "tipo": "condicional",
        "emisor": "Servicio Agrícola y Ganadero (SAG)",
        "pista": "resolución del SAG que aprueba la subdivisión predial (parcelas D.L. 3.516)",
        "vigencia_dias": None,
    },
    {
        "codigo": "plano_subdivision_sag",
        "nombre_largo": "Plano de Subdivisión SAG",
        "categoria": "rural",
        "tipo": "condicional",
        "emisor": "Servicio Agrícola y Ganadero (SAG) / CBR",
        "pista": "plano de subdivisión visado por el SAG y archivado en el CBR",
        "vigencia_dias": None,
    },
    # ---------- LEY INDÍGENA (condicional) ----------
    {
        "codigo": "cert_conadi",
        "nombre_largo": "Certificado CONADI",
        "categoria": "indigena",
        "tipo": "condicional",
        "emisor": "CONADI",
        "pista": "certificado de la Corporación Nacional de Desarrollo Indígena que acredita calidad indígena (Ley 19.253)",
        "vigencia_dias": None,
    },
    # ---------- HERENCIAS (condicional) ----------
    {
        "codigo": "cert_posesion_efectiva",
        "nombre_largo": "Posesión Efectiva",
        "categoria": "herencia",
        "tipo": "condicional",
        "emisor": "Registro Civil / Tribunal",
        "pista": "auto o resolución de posesión efectiva (testada por el Registro Civil o intestada por Tribunal)",
        "vigencia_dias": None,
    },
    {
        "codigo": "cert_exencion_herencia_sii",
        "nombre_largo": "Certificado de Impuesto a Herencias",
        "categoria": "herencia",
        "tipo": "condicional",
        "emisor": "Servicio de Impuestos Internos (SII)",
        "pista": "certificado del SII sobre exención o pago del impuesto a las herencias",
        "vigencia_dias": None,
    },
    # ---------- PLANOS / NORMATIVA URBANA (legacy + plan regulador) ----------
    {
        "codigo": "plano_propiedad",
        "nombre_largo": "Plano de Propiedad / Loteo",
        "categoria": "urbanismo",
        "tipo": "condicional",
        "emisor": "Arquitecto / DOM / CBR",
        "pista": "plano arquitectónico/topográfico con cotas, deslindes y superficie",
        "vigencia_dias": None,
    },
    {
        "codigo": "plan_regulador",
        "nombre_largo": "Plan Regulador Comunal",
        "categoria": "urbanismo",
        "tipo": "condicional",
        "emisor": "Municipalidad",
        "pista": "instrumento de planificación territorial con zonificación y normativa urbana",
        "vigencia_dias": None,
    },
    # ---------- FALLBACK ----------
    {
        "codigo": "otro",
        "nombre_largo": "Otro",
        "categoria": "otro",
        "tipo": "condicional",
        "emisor": "—",
        "pista": "no encaja en ninguna de las categorías anteriores",
        "vigencia_dias": None,
    },
]


# Útiles ---------------------------------------------------------------------

CATALOGO_BY_CODIGO: dict[str, TipoDocCatalogo] = {t["codigo"]: t for t in CATALOGO}

# Lista de códigos válidos (incluye 'otro')
CODIGOS_VALIDOS: list[str] = [t["codigo"] for t in CATALOGO]

# Documentos base (siempre se piden al inicio del estudio)
TIPOS_BASE: list[TipoDocCatalogo] = [t for t in CATALOGO if t["tipo"] == "base"]

# Documentos condicionales (se piden vía trigger al detectar condiciones)
TIPOS_CONDICIONALES: list[TipoDocCatalogo] = [t for t in CATALOGO if t["tipo"] == "condicional"]
