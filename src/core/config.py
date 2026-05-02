"""
clasificador-api · configuración centralizada
--------------------------------------------------------------
Settings vía python-decouple (compatible con .env). Mismo patrón
que sintetizador-api/clasificador-api de ISA1.
"""
from decouple import config


class Settings:
    # Servicio
    PORT: int = config("PORT", default=8084, cast=int)
    ENVIRONMENT: str = config("NODE_ENV", default="production")
    SERVICE_NAME: str = "clasificador-api"
    SERVICE_VERSION: str = config("SERVICE_VERSION", default="0.0.1")
    LOG_LEVEL: str = config("LOG_LEVEL", default="INFO")
    SKIP_AUTH: bool = config("SKIP_AUTH", default=False, cast=bool)

    # GCP
    GOOGLE_CLOUD_PROJECT: str = config("GOOGLE_CLOUD_PROJECT", default="")
    GCS_BUCKET_DOCUMENTOS: str = config("GCS_BUCKET_DOCUMENTOS", default="hipotecai-documentos")

    # Vertex AI (preferido). Auth via SA del Cloud Run con roles/aiplatform.user.
    # Si VERTEX_AI=false cae a Generative Language API con GEMINI_API_KEY.
    VERTEX_AI: bool = config("VERTEX_AI", default=True, cast=bool)
    VERTEX_LOCATION: str = config("VERTEX_LOCATION", default="global")

    # langextract → Gemini (fallback)
    GEMINI_API_KEY: str = config("GEMINI_API_KEY", default="")
    GEMINI_MODEL_ID: str = config("GEMINI_MODEL_ID", default="gemini-2.5-flash")
    LANGEXTRACT_TEMPERATURE: float = config("LANGEXTRACT_TEMPERATURE", default=0.1, cast=float)

    # OAuth (cuando viene tras gateway)
    GOOGLE_CLIENT_ID: str = config("GOOGLE_CLIENT_ID", default="")

    # Postgres / Cloud SQL
    DB_HOST: str = config("DB_HOST", default="localhost")
    DB_USER: str = config("DB_USER", default="postgres")
    DB_PASSWORD: str = config("DB_PASSWORD", default="")
    DB_NAME: str = config("DB_NAME", default="hipotecai")
    DB_PORT: int = config("DB_PORT", default=5432, cast=int)
    INSTANCE_CONNECTION_NAME: str = config("INSTANCE_CONNECTION_NAME", default="")

    # CORS
    ALLOWED_ORIGINS: list = config(
        "ALLOWED_ORIGINS",
        default="http://localhost:3030,http://localhost:3000",
        cast=lambda v: [s.strip() for s in v.split(",")],
    )


settings = Settings()
