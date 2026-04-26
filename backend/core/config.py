from typing import List, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "AutoTwin AI"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False

    # Security
    SECRET_KEY: str = "VVPChl1amGDVNr4BjemaIEN-OoC0XQ11aEyNy258dNY"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # ── Supabase / PostgreSQL ─────────────────────────────────
    DATABASE_URL: str = ""          # postgresql+asyncpg://...
    SUPABASE_URL: str = ""          # https://xxx.supabase.co
    SUPABASE_ANON_KEY: str = ""     # public anon key
    SUPABASE_SERVICE_ROLE_KEY: str = ""  # service role (server-side only)
    SUPABASE_STORAGE_BUCKET: str = "invoices"

    # CORS Configuration
    CORS_ORIGINS: List[str] = ["https://autotwin-one.vercel.app", "http://localhost:3000"]

    # WhatsApp Integration (Official Meta Cloud API)
    WHATSAPP_CLOUD_TOKEN: str = ""
    WHATSAPP_PHONE_NUMBER_ID: str = ""
    WHATSAPP_VERIFY_TOKEN: str = "autotwin_secret_webhook_token"
    WHATSAPP_DEFAULT_NUMBER: str = "917036970010"  # Fallback when DB phone lookup fails

    # AutoTwin AI Thresholds
    HIGH_CONFIDENCE_THRESHOLD: float = 0.95
    MEDIUM_CONFIDENCE_THRESHOLD: float = 0.70

    # API Keys
    GEMINI_API_KEY: Optional[str] = None
    GROQ_API_KEY: Optional[str] = None

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
