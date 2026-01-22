import logging
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Integer, DateTime, Text, JSON
from datetime import datetime
from config import get_settings

settings = get_settings()
logger = logging.getLogger("database")

# إعداد المحرك غير المتزامن
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    connect_args={"check_same_thread": False} if "sqlite" in settings.DATABASE_URL else {}
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

class Base(DeclarativeBase):
    pass

# نموذج سجل الطلبات
class RequestLog(Base):
    __tablename__ = "request_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    url: Mapped[str] = mapped_column(Text)
    format_type: Mapped[str] = mapped_column(String(10))
    quality: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    
    # Metadata
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration: Mapped[int | None] = mapped_column(Integer, nullable=True)
    filesize: Mapped[int | None] = mapped_column(Integer, nullable=True)
    download_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_msg: Mapped[str | None] = mapped_column(Text, nullable=True)
    
    # Audit
    client_ip: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

async def init_db():
    """إنشاء الجداول مع معالجة الأخطاء في حال وجودها مسبقاً"""
    try:
        async with engine.begin() as conn:
            # checkfirst=True يحاول التأكد من عدم وجود الجدول قبل إنشائه
            await conn.run_sync(Base.metadata.create_all, checkfirst=True)
    except Exception as e:
        # إذا حدث خطأ لأن الجدول موجود، نقوم بتجاهله وإكمال التشغيل
        if "already exists" in str(e):
            logger.warning("Database tables already exist. Skipping creation.")
        else:
            logger.error(f"Database initialization error: {e}")
            # لا نوقف السيرفر، بل نكمل (قد يكون خطأ بسيط)

async def get_db():
    async with SessionLocal() as session:
        yield session
