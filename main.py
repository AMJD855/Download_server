import uuid
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

# استيراد الوحدات الخاصة بنا
from config import get_settings
from database import init_db, get_db, RequestLog
from services import YtDlpService
from pydantic import BaseModel, HttpUrl, field_validator

# إعداد الـ Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("api")
settings = get_settings()

# --- Pydantic Schemas ---
class VideoRequest(BaseModel):
    url: str
    format_type: str = "video"
    quality: str = "720"

    @field_validator('format_type')
    def validate_type(cls, v):
        if v not in ['video', 'audio']:
            raise ValueError("must be 'video' or 'audio'")
        return v

class VideoResponse(BaseModel):
    request_id: str
    status: str
    data: dict | None = None
    error: str | None = None

# --- Lifespan (Startup/Shutdown) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # تهيئة قاعدة البيانات عند البدء
    await init_db()
    yield
    # تنظيف الموارد عند الإغلاق (إن وجد)

# --- App Init ---
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.VERSION,
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Helper Functions ---
async def save_log(db: AsyncSession, req_id: str, payload: VideoRequest, result: dict, request: Request):
    """حفظ السجل في قاعدة البيانات بشكل منفصل"""
    try:
        log_entry = RequestLog(
            id=req_id,
            url=str(payload.url),
            format_type=payload.format_type,
            quality=payload.quality,
            status=result.get("status", "error"),
            title=result.get("title"),
            duration=result.get("duration"),
            filesize=result.get("filesize"),
            download_url=result.get("url"),
            error_msg=result.get("error"),
            client_ip=request.client.host,
            user_agent=request.headers.get("user-agent")
        )
        db.add(log_entry)
        await db.commit()
    except Exception as e:
        logger.error(f"Failed to save log: {e}")

# --- Endpoints ---

@app.post("/api/v1/extract", response_model=VideoResponse)
async def extract_video(
    payload: VideoRequest, 
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """
    نقطة النهاية الرئيسية لاستخراج روابط الفيديو.
    """
    request_id = str(uuid.uuid4())
    
    # 1. استدعاء الخدمة (العملية الثقيلة)
    # ملاحظة: يتم تشغيلها في ThreadPool داخل Service لعدم تعطيل السيرفر
    result = await YtDlpService.process_url(
        payload.url, 
        payload.format_type, 
        payload.quality
    )
    
    # 2. تسجيل العملية في الخلفية (Fire and Forget)
    # نمرر دالة wrapper لأن background_tasks تحتاج دالة عادية أو async
    async def log_wrapper():
        # نحتاج session جديدة للمهمة الخلفية لأن الـ session الحالية ستغلق بعد الرد
        async for session in get_db():
            await save_log(session, request_id, payload, result, request)
            break
            
    background_tasks.add_task(log_wrapper)

    if result["status"] == "error":
        return VideoResponse(
            request_id=request_id,
            status="error",
            error=result.get("error")
        )

    return VideoResponse(
        request_id=request_id,
        status="success",
        data=result
    )

@app.get("/api/v1/stats")
async def get_stats(db: AsyncSession = Depends(get_db)):
    """إحصائيات بسيطة"""
    try:
        total = await db.scalar(select(func.count(RequestLog.id)))
        success = await db.scalar(select(func.count(RequestLog.id)).where(RequestLog.status == 'success'))
        
        return {
            "total_requests": total,
            "success_rate": f"{(success/total*100):.1f}%" if total > 0 else "0%",
            "successful_requests": success
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health():
    return {"status": "healthy", "version": settings.VERSION}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app", 
        host=settings.HOST, 
        port=settings.PORT, 
        reload=settings.DEBUG
    )
