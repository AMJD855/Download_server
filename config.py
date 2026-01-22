from pydantic_settings import BaseSettings
from functools import lru_cache
import os

class Settings(BaseSettings):
    APP_NAME: str = "Video Download API"
    VERSION: str = "2.1.0"
    DEBUG: bool = False
    
    # Server Config
    # Render يعطي المنفذ عبر متغير بيئة، لذلك يجب قراءته
    HOST: str = "0.0.0.0"
    PORT: int = int(os.getenv("PORT", 8000))
    
    # Database
    # استخدام مسار مطلق لضمان العمل بشكل صحيح
    # إذا كنت تستخدم Render Disk، غير المسار إلى /var/data/production.db
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///production.db")
    
    # Worker Config
    MAX_WORKERS: int = 4
    
    class Config:
        env_file = ".env"

@lru_cache()
def get_settings():
    return Settings()
