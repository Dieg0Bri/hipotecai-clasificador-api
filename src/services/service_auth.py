"""
service_auth · obtiene id_tokens de service account para llamadas s2s.

El metadata server de Cloud Run firma un id_token con `aud = <URL del servicio>`.
El servicio destino lo valida y obtiene el email del SA invocador (la SA que
corre este Cloud Run). Tokens dura 1h — los cacheamos por audience para no
pegarle al metadata server en cada request.

Si no estamos en GCP (dev local), `fetch_id_token` cae a Application Default
Credentials (ej. `gcloud auth application-default login` con un user). En ese
caso el destino debería estar con SKIP_AUTH=true.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Refrescamos con margen: el token dura 1h, lo invalidamos a los 50 min.
_TOKEN_TTL_S = 50 * 60

_cache: dict[str, tuple[str, float]] = {}
_lock = threading.Lock()


def get_id_token(audience: str) -> Optional[str]:
    """Devuelve un id_token válido para `audience`. None si no se pudo obtener.

    `audience` debe ser la URL del servicio destino sin path (ej.
    "https://ocr-api-xxx.run.app"). El servicio destino lo valida contra esa
    misma cadena.
    """
    if not audience:
        return None
    audience = audience.rstrip("/")
    now = time.time()
    with _lock:
        cached = _cache.get(audience)
        if cached and cached[1] > now:
            return cached[0]

    try:
        from google.auth.transport import requests as g_requests  # type: ignore
        from google.oauth2 import id_token as gid_token  # type: ignore
    except ImportError:
        logger.error("google-auth no instalado — no puedo firmar id_token s2s.")
        return None

    try:
        token = gid_token.fetch_id_token(g_requests.Request(), audience)
    except Exception as exc:  # noqa: BLE001
        logger.warning("fetch_id_token(%s) falló: %s", audience, exc)
        return None

    with _lock:
        _cache[audience] = (token, now + _TOKEN_TTL_S)
    return token
