import uuid
import logging
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

# استيراد ملفات المشروع الداخلية
from config import get_settings
from database import init_db, get_db, RequestLog
from services import YtDlpService
from pydantic import BaseModel, field_validator

# إعداد السجلات (Logging)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("api")
settings = get_settings()

# --- نماذج البيانات (Pydantic Schemas) ---
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

# --- إدارة دورة حياة التطبيق (Lifespan) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. كود يعمل عند تشغيل السيرفر
    logger.info("Initializing Database...")
    await init_db()
    logger.info(f"Server started on port {settings.PORT}")
    
    yield
    
    # 2. كود يعمل عند إغلاق السيرفر (تنظيف)
    logger.info("Server shutting down...")

# --- إعداد التطبيق ---
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.VERSION,
    lifespan=lifespan
)

# تفعيل CORS للسماح بالطلبات من أي مكان
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- الوظائف المساعدة ---
async def save_log(db: AsyncSession, req_id: str, payload: VideoRequest, result: dict, request: Request):
    """حفظ السجل في قاعدة البيانات كخلفية (Background Task)"""
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
            client_ip=request.client.host if request.client else "unknown",
            user_agent=request.headers.get("user-agent")
        )
        db.add(log_entry)
        await db.commit()
    except Exception as e:
        logger.error(f"Failed to save log: {e}")

# --- نقاط النهاية (Endpoints) ---

@app.api_route("/ping", methods=["GET", "HEAD"])
async def ping(request: Request):
    """
    نقطة فحص الاتصال:
    - HEAD: يرجع 200 فقط (لفحص العمل).
    - GET: يرجع تفاصيل الحالة.
    """
    if request.method == "HEAD":
        # استجابة سريعة لأدوات المراقبة
        return Response(status_code=200)
    
    return {
        "status": "online",
        "service": settings.APP_NAME,
        "version": settings.VERSION,
        "mode": "debug" if settings.DEBUG else "production"
    }

@app.post("/api/v1/extract", response_model=VideoResponse)
async def extract_video(
    payload: VideoRequest, 
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """استخراج رابط الفيديو وتشغيله في الخلفية"""
    request_id = str(uuid.uuid4())
    
    # معالجة الرابط (Logic)
    result = await YtDlpService.process_url(
        payload.url, 
        payload.format_type, 
        payload.quality
    )
    
    # دالة لحفظ السجل وتمرير الـ Session بشكل آمن
    async def log_wrapper():
        async for session in get_db():
            await save_log(session, request_id, payload, result, request)
            break
            
    # إضافة المهمة للخلفية (Fire and Forget)
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
    """جلب إحصائيات الاستخدام"""
    try:
        # حساب العدد الكلي
        total_query = select(func.count(RequestLog.id))
        total = await db.scalar(total_query) or 0
        
        # حساب عدد الطلبات الناجحة
        success_query = select(func.count(RequestLog.id)).where(RequestLog.status == 'success')
        success = await db.scalar(success_query) or 0
        
        return {
            "total_requests": total,
            "success_rate": f"{(success/total*100):.1f}%" if total > 0 else "0%",
            "successful_requests": success
        }
    except Exception as e:
        logger.error(f"Stats error: {e}")
        # إرجاع قيم صفرية بدلاً من الخطأ في حالة عدم جاهزية الـ DB
        return {"total_requests": 0, "success_rate": "0%", "note": "Database stats unavailable"}

@app.get("/health")
async def health():
    """فحص صحة السيرفر (Health Check) لـ Render"""
    return {"status": "healthy"}

# --- نقطة التشغيل الرئيسية ---
if __name__ == "__main__":
    # هذا الجزء مهم جداً لـ Render
    # يقوم بقراءة البورت من الإعدادات (التي تقرؤه من متغيرات البيئة)
    uvicorn.run(
        "main:app", 
        host=settings.HOST, 
        port=settings.PORT, 
        reload=settings.DEBUG
    )
