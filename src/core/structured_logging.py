"""
Structured logging — JSON a stdout en Cloud Run, formato legible
en local. GCP Cloud Logging lo captura nativamente.
"""
import json
import logging
import os
import sys
from datetime import datetime

from src.core.config import settings


_IS_CLOUD_RUN = bool(os.environ.get("K_SERVICE"))

_SEVERITY = {
    logging.DEBUG: "DEBUG",
    logging.INFO: "INFO",
    logging.WARNING: "WARNING",
    logging.ERROR: "ERROR",
    logging.CRITICAL: "CRITICAL",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "severity": _SEVERITY.get(record.levelno, "DEFAULT"),
            "message": record.getMessage(),
            "service": settings.SERVICE_NAME,
            "version": settings.SERVICE_VERSION,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "logger": record.name,
        }
        if record.exc_info:
            payload["stack_trace"] = self.formatException(record.exc_info)
        for k, v in record.__dict__.items():
            if k.startswith("ctx_"):
                payload[k[4:]] = v
        return json.dumps(payload)


def configure_logging() -> None:
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))

    handler = logging.StreamHandler(sys.stdout)
    if _IS_CLOUD_RUN:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(name)s] %(levelname)s: %(message)s",
            datefmt="%H:%M:%S",
        ))
    root.addHandler(handler)
