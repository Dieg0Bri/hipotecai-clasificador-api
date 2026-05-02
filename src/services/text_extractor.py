"""
Extracción de texto de PDFs/imágenes para alimentar al clasificador.
Usa pdfplumber para PDFs nativos. Para PDFs escaneados se requiere
OCR (no incluido en v0; ver TODO en /classify-from-gcs).
"""
import io
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def extract_text_from_pdf(content: bytes, max_pages: int = 12) -> str:
    """Extrae texto plano de un PDF nativo. Soporta hasta `max_pages` páginas."""
    try:
        import pdfplumber  # noqa: WPS433
    except ImportError as exc:
        raise RuntimeError("pdfplumber no instalado. pip install pdfplumber") from exc

    text_parts: list[str] = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for i, page in enumerate(pdf.pages):
            if i >= max_pages:
                break
            page_text = page.extract_text() or ""
            text_parts.append(page_text.strip())

    full_text = "\n\n".join(t for t in text_parts if t)
    if not full_text.strip():
        logger.warning("PDF parece escaneado (sin texto seleccionable). Requiere OCR.")
    return full_text


def extract_text_from_bytes(content: bytes, mime_type: str | None = None, filename: str | None = None) -> str:
    """Despachador genérico — por ahora sólo PDF. Imágenes → TODO OCR."""
    ext = (Path(filename).suffix.lower() if filename else "")
    if (mime_type and "pdf" in mime_type) or ext == ".pdf":
        return extract_text_from_pdf(content)

    # Texto plano (.txt, .md)
    if (mime_type and mime_type.startswith("text/")) or ext in {".txt", ".md"}:
        return content.decode("utf-8", errors="ignore")

    # Imágenes y otros → fuera de v0
    raise NotImplementedError(
        f"Extracción de texto no soportada para mime={mime_type} ext={ext}. "
        "Para escaneos integrar Document AI OCR o pytesseract en una iteración futura."
    )
