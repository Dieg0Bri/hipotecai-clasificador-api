# clasificador-api

Servicio Python/FastAPI que clasifica documentos hipotecarios chilenos en categorías legales (escritura, certificado CBR, certificado SII, certificado municipal, plano, plan regulador, otro). Usa **langextract** sobre **Gemini 2.5 Flash** para que cada extracción quede anclada al fragmento del texto fuente — auditabilidad legal.

## Endpoints

| Método | Ruta                  | Descripción |
|--------|-----------------------|-------------|
| GET    | `/health`             | health + estado de Gemini y CloudSQL |
| POST   | `/classify-text`      | Clasifica un texto plano (UI / debug) |
| POST   | `/classify-from-gcs`  | Descarga el objeto del bucket, extrae texto, clasifica y persiste en `dt_archivos` |
| POST   | `/`                   | Receptor Eventarc (GCS object finalize) |

## Categorías reconocidas

| Código | Nombre largo |
|--------|--------------|
| `escritura`                   | Escritura pública (compraventa, hipoteca, alzamiento) |
| `cert_dominio_vigente`        | Certificado de dominio vigente (CBR) |
| `cert_hipotecas_gravamenes`   | Certificado de hipotecas y gravámenes (CBR) |
| `cert_avaluo_sii`             | Certificado de avalúo fiscal (SII) |
| `cert_municipal`              | Certificado municipal (número, no expropiación, recepción final) |
| `plano_propiedad`             | Plano de propiedad / loteo |
| `plan_regulador`              | Plan regulador comunal |
| `otro`                        | Sin clasificar — requiere revisión |

## Modos de clasificación

1. **Con `GEMINI_API_KEY`**: usa langextract + Gemini con few-shot prompting (3 ejemplos representativos) y devuelve confianza + spans del texto fuente.
2. **Sin API key (fallback heurístico)**: clasificación por palabras clave (rule-based). Confianza máxima 0.55, siempre marca `requiere_revision=true`.

## Desarrollo

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env  # configurar GEMINI_API_KEY (Google AI Studio)
uvicorn main:app --reload --host 0.0.0.0 --port 8084
```

## Costo y latencia

`gemini-2.5-flash` ~ $0.075 / 1M input tokens, $0.30 / 1M output. Un certificado típico (~3K tokens) cuesta ~$0.0002 y se clasifica en ~1-2s.

## Trazabilidad

Cada clasificación incluye `spans_evidencia` con los offsets `(start_char, end_char)` del fragmento que justifica la decisión. El frontend usa esto para resaltar los pasajes en el visor PDF y mantener auditabilidad.
