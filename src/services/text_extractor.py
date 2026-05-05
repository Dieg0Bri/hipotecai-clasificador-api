"""
Extracción de texto de PDFs con soporte de OCR por página.

Mismo módulo que documentos-api/src/services/text_extractor.py — sin monorepo,
los servicios siblings copian su utilidad. Mantener ambos sincronizados al
modificar.

El caso típico chileno es un documento mixto: la primera página es una
carátula notarial generada digitalmente y desde la página 2 viene la
escritura escaneada. Este módulo devuelve UNA entrada por página y marca
las vacías (`source='empty'`) para que el caller decida qué páginas mandar
a OCR sin tirar el texto digital ya disponible.
"""
import io
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

logger = logging.getLogger(__name__)


class PageOffset(NamedTuple):
    page: int        # 1-based
    start: int
    end: int


@dataclass
class PageContent:
    page: int                          # 1-based
    text: str                          # "" si no se pudo extraer
    source: str = "empty"              # "pdf_text" | "ocr" | "empty"
    id_ocr: int | None = None
    confianza_ocr_promedio: float | None = None


_EMPTY_PAGE_THRESHOLD = 20


def _is_effectively_empty(text: str) -> bool:
    if not text:
        return True
    alnum = sum(1 for c in text if c.isalnum())
    return alnum < _EMPTY_PAGE_THRESHOLD


def extract_pages_from_pdf(content: bytes, max_pages: int = 60) -> list[PageContent]:
    try:
        import pdfplumber
    except ImportError as exc:
        raise RuntimeError("pdfplumber no instalado. pip install pdfplumber") from exc

    pages: list[PageContent] = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for i, page in enumerate(pdf.pages):
            if i >= max_pages:
                break
            text = (page.extract_text() or "").strip()
            if _is_effectively_empty(text):
                pages.append(PageContent(page=i + 1, text="", source="empty"))
            else:
                pages.append(PageContent(page=i + 1, text=text, source="pdf_text"))
    return pages


def extract_pages_from_bytes(
    content: bytes, mime_type: str | None = None, filename: str | None = None
) -> list[PageContent]:
    ext = (Path(filename).suffix.lower() if filename else "")
    if (mime_type and "pdf" in mime_type) or ext == ".pdf":
        return extract_pages_from_pdf(content)
    if (mime_type and mime_type.startswith("text/")) or ext in {".txt", ".md"}:
        text = content.decode("utf-8", errors="ignore")
        return [PageContent(page=1, text=text, source="pdf_text" if text.strip() else "empty")]
    if (mime_type and mime_type.startswith("image/")) or ext in {".png", ".jpg", ".jpeg", ".tif", ".tiff"}:
        return [PageContent(page=1, text="", source="empty")]
    raise NotImplementedError(
        f"Extracción de texto no soportada para mime={mime_type} ext={ext}."
    )


def assemble_text_and_offsets(pages: list[PageContent]) -> tuple[str, list[PageOffset]]:
    SEP = "\n\n"
    parts: list[str] = []
    offsets: list[PageOffset] = []
    cursor = 0
    for p in pages:
        if not p.text:
            continue
        if parts:
            cursor += len(SEP)
        start = cursor
        end = cursor + len(p.text)
        offsets.append(PageOffset(page=p.page, start=start, end=end))
        parts.append(p.text)
        cursor = end
    return SEP.join(parts), offsets


# ───── Estadísticas por página (para que Gemini decida OCR) ─────

def page_stats(pages: list[PageContent]) -> dict:
    """Resumen compacto de qué pasó al extraer texto. Lo pasamos a Gemini
    para que tome la decisión de OCR de forma informada — no con una
    heurística mágica del lado del código.

    Devuelve algo como:
        {
          "n_total": 30,
          "n_con_texto": 1,
          "n_vacias": 29,
          "paginas_con_texto": [1],
          "paginas_vacias": [2,3,4,...,30],
          "chars_por_pagina": [1234, 0, 0, ...]
        }
    """
    con_texto = [p.page for p in pages if p.source == "pdf_text"]
    vacias = [p.page for p in pages if p.source == "empty"]
    return {
        "n_total": len(pages),
        "n_con_texto": len(con_texto),
        "n_vacias": len(vacias),
        "paginas_con_texto": con_texto,
        "paginas_vacias": vacias,
        "chars_por_pagina": [
            sum(1 for c in p.text if c.isalnum()) for p in pages
        ],
    }


# ───── Compatibilidad con el contrato anterior ─────

def extract_text_from_pdf(content: bytes, max_pages: int = 12) -> str:
    pages = extract_pages_from_pdf(content, max_pages=max_pages)
    text, _ = assemble_text_and_offsets(pages)
    if not text.strip():
        logger.warning("PDF parece escaneado (sin texto seleccionable). Requiere OCR.")
    return text


def extract_text_from_bytes(content: bytes, mime_type: str | None = None, filename: str | None = None) -> str:
    pages = extract_pages_from_bytes(content, mime_type=mime_type, filename=filename)
    text, _ = assemble_text_and_offsets(pages)
    return text
