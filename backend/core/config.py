from typing import List, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    APP_NAME: str = "AutoTwin AI"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False

    # Security
    SECRET_KEY: str = "your-secret-key-change-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # Database Configuration
    MONGODB_URL: str = "mongodb://localhost:27017"
    DATABASE_NAME: str = "autotwin"

    # Optional Services
    REDIS_URL: Optional[str] = "redis://localhost:6379"

    # CORS Configuration
    CORS_ORIGINS: List[str] = ["https://autotwin-one.vercel.app", "http://localhost:3000"]

    # AutoTwin AI Thresholds
    HIGH_CONFIDENCE_THRESHOLD: float = 0.95
    MEDIUM_CONFIDENCE_THRESHOLD: float = 0.70

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

settings = Settings()
