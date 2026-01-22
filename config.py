from pydantic_settings import BaseSettings
from functools import lru_cache
import os

class Settings(BaseSettings):
    APP_NAME: str = "Video Download API"
    VERSION: str = "2.0.0"
    DEBUG: bool = False
    
    # Server Config
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    
    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./production.db"
    
    # Worker Config (للتحكم في عدد العمليات المتزامنة لـ yt-dlp)
    MAX_WORKERS: int = 4
    
    class Config:
        env_file = ".env"

@lru_cache()
def get_settings():
    return Settings()
