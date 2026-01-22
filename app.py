"""
التطبيق الرئيسي لسيرفر تحميل الفيديو
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from urllib.parse import urlparse, parse_qs

from fastapi import FastAPI, Request, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
import yt_dlp
import sqlite3
import aiosqlite
import uuid
import httpx
from pydantic import BaseModel, HttpUrl, field_validator
import os
import random

# إعداد التطبيق
app = FastAPI(
    title="Video Download Server",
    description="سيرفر استخراج روابط الفيديو باستخدام yt-dlp",
    version="1.0.0",
    docs_url=None,  # إغلاق صفحة التوثيق
    redoc_url=None  # إغلاق صفحة التوثيق
)

# إعداد CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# إعدادات قاعدة البيانات
DATABASE_URL = "sqlite:///video_server.db"
DB_FILE = "video_server.db"

# نماذج البيانات
class VideoRequest(BaseModel):
    """نموذج طلب تحميل الفيديو"""
    url: str
    format_type: str = "video"  # video أو audio
    quality: str = "720"  # 360, 480, 720, best
    custom_options: Optional[Dict[str, Any]] = None

    @field_validator('format_type')
    @classmethod
    def validate_format_type(cls, v):
        if v not in ["video", "audio"]:
            raise ValueError('format_type must be either "video" or "audio"')
        return v

    @field_validator('quality')
    @classmethod
    def validate_quality(cls, v):
        valid_qualities = ["360", "480", "720", "best"]
        if v not in valid_qualities:
            raise ValueError(f'quality must be one of {valid_qualities}')
        return v

class VideoResponse(BaseModel):
    """نموذج استجابة تحميل الفيديو"""
    status: str
    video_id: str
    download_url: Optional[str] = None
    title: Optional[str] = None
    duration: Optional[int] = None
    thumbnail: Optional[str] = None
    format: Optional[str] = None
    filesize: Optional[int] = None
    error: Optional[str] = None

class PingResponse(BaseModel):
    """نموذج استجابة ping"""
    status: str
    service: str
    time: str
    version: str

# إعدادات yt-dlp
YTDL_OPTIONS = {
    'quiet': True,
    'no_warnings': True,
    'extract_flat': False,
    'force_ipv4': True,
}

# قائمة بوكلاء IP افتراضية (يمكن إضافة المزيد)
PROXY_LIST = [
    None,  # بدون بروكسي
    # يمكن إضافة بروكسيات هنا إذا توفرت
]

# إعداد التسجيل
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# وظائف قاعدة البيانات
def init_db():
    """تهيئة قاعدة البيانات"""
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        # إنشاء جدول الطلبات
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS requests (
                id TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                format_type TEXT NOT NULL,
                quality TEXT NOT NULL,
                status TEXT NOT NULL,
                download_url TEXT,
                title TEXT,
                duration INTEGER,
                thumbnail TEXT,
                format TEXT,
                filesize INTEGER,
                client_ip TEXT,
                user_agent TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # إنشاء جدول السجلات
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT,
                action TEXT,
                details TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (request_id) REFERENCES requests (id)
            )
        ''')
        
        conn.commit()
        conn.close()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Database initialization error: {e}")
        raise

async def save_request_to_db(request_id: str, video_request: VideoRequest, 
                           client_ip: str, user_agent: str):
    """حفظ الطلب في قاعدة البيانات"""
    try:
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute('''
                INSERT INTO requests 
                (id, url, format_type, quality, status, client_ip, user_agent)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                request_id, 
                video_request.url, 
                video_request.format_type,
                video_request.quality,
                "pending",
                client_ip,
                user_agent
            ))
            await db.commit()
            
            # تسجيل العملية
            await db.execute('''
                INSERT INTO logs (request_id, action, details)
                VALUES (?, ?, ?)
            ''', (request_id, "request_received", json.dumps({
                "url": video_request.url,
                "format_type": video_request.format_type,
                "quality": video_request.quality
            })))
            await db.commit()
            
            logger.info(f"Request saved to database: {request_id}")
    except Exception as e:
        logger.error(f"Error saving request to database: {e}")

async def update_request_in_db(request_id: str, video_response: VideoResponse):
    """تحديث الطلب في قاعدة البيانات"""
    try:
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute('''
                UPDATE requests 
                SET status = ?, download_url = ?, title = ?, 
                    duration = ?, thumbnail = ?, format = ?, 
                    filesize = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (
                video_response.status,
                video_response.download_url,
                video_response.title,
                video_response.duration,
                video_response.thumbnail,
                video_response.format,
                video_response.filesize,
                request_id
            ))
            await db.commit()
            
            # تسجيل العملية
            action = "success" if video_response.status == "success" else "error"
            await db.execute('''
                INSERT INTO logs (request_id, action, details)
                VALUES (?, ?, ?)
            ''', (request_id, action, json.dumps(video_response.dict())))
            await db.commit()
            
            logger.info(f"Request updated in database: {request_id}")
    except Exception as e:
        logger.error(f"Error updating request in database: {e}")

async def get_request_from_db(request_id: str):
    """استرجاع الطلب من قاعدة البيانات"""
    try:
        async with aiosqlite.connect(DB_FILE) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute('''
                SELECT * FROM requests WHERE id = ?
            ''', (request_id,))
            row = await cursor.fetchone()
            
            if row:
                return dict(row)
            return None
    except Exception as e:
        logger.error(f"Error getting request from database: {e}")
        return None

# وظائف yt-dlp
def get_ydl_options(format_type: str, quality: str, custom_options: Optional[Dict] = None):
    """الحصول على خيارات yt-dlp بناءً على التفضيلات"""
    options = YTDL_OPTIONS.copy()
    
    if format_type == "audio":
        options.update({
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
        })
    else:
        # تحديد الجودة بناءً على الاختيار
        quality_map = {
            "360": "best[height<=360]",
            "480": "best[height<=480]",
            "720": "best[height<=720]",
            "best": "best"
        }
        
        format_filter = quality_map.get(quality, "best")
        options.update({
            'format': format_filter,
        })
    
    # إضافة خيارات مخصصة إذا وجدت
    if custom_options:
        options.update(custom_options)
    
    return options

def extract_video_info(url: str, options: Dict) -> Dict:
    """استخراج معلومات الفيديو باستخدام yt-dlp"""
    try:
        # اختيار بروكسي عشوائي لتجنب الحظر
        proxy = random.choice(PROXY_LIST)
        if proxy:
            options['proxy'] = proxy
            logger.info(f"Using proxy: {proxy}")
        
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
            
            result = {
                'title': info.get('title', 'Unknown'),
                'duration': info.get('duration', 0),
                'thumbnail': info.get('thumbnail', ''),
                'formats': []
            }
            
            # استخراج المعلومات المناسبة
            if 'requested_formats' in info and len(info['requested_formats']) > 0:
                for fmt in info['requested_formats']:
                    result['formats'].append({
                        'url': fmt.get('url'),
                        'format_id': fmt.get('format_id'),
                        'ext': fmt.get('ext'),
                        'filesize': fmt.get('filesize'),
                        'format_note': fmt.get('format_note', ''),
                        'resolution': fmt.get('resolution', ''),
                        'height': fmt.get('height'),
                        'width': fmt.get('width')
                    })
            elif 'url' in info:
                result['formats'].append({
                    'url': info.get('url'),
                    'format_id': info.get('format_id', ''),
                    'ext': info.get('ext', ''),
                    'filesize': info.get('filesize'),
                    'format_note': info.get('format_note', ''),
                    'resolution': info.get('resolution', ''),
                    'height': info.get('height'),
                    'width': info.get('width')
                })
            
            return result
    except Exception as e:
        logger.error(f"Error extracting video info: {e}")
        raise

def process_video_request(request_data: VideoRequest) -> VideoResponse:
    """معالجة طلب الفيديو"""
    request_id = str(uuid.uuid4())
    
    try:
        # الحصول على خيارات yt-dlp
        options = get_ydl_options(
            request_data.format_type,
            request_data.quality,
            request_data.custom_options
        )
        
        # استخراج معلومات الفيديو
        video_info = extract_video_info(request_data.url, options)
        
        # اختيار أفضل تنسيق
        if not video_info['formats']:
            raise Exception("No suitable formats found")
        
        # اختيار التنسيق الأول (الأفضل بناءً على الخيارات)
        selected_format = video_info['formats'][0]
        
        response = VideoResponse(
            status="success",
            video_id=request_id,
            download_url=selected_format['url'],
            title=video_info['title'],
            duration=video_info['duration'],
            thumbnail=video_info['thumbnail'],
            format=f"{selected_format.get('format_note', '')} ({selected_format.get('ext', '')})",
            filesize=selected_format.get('filesize')
        )
        
        return response
        
    except Exception as e:
        logger.error(f"Error processing video request: {e}")
        return VideoResponse(
            status="error",
            video_id=request_id,
            error=str(e)
        )

# نقط النهاية
@app.api_route("/ping", methods=["GET", "HEAD", "POST"])
async def ping(request: Request):
    """فحص حالة السيرفر"""
    # في حالة HEAD لا نرجع body
    if request.method == "HEAD":
        return JSONResponse(content=None, status_code=200)
    
    return PingResponse(
        status="online",
        service="video-download-server",
        time=datetime.utcnow().isoformat(),
        version="1.0.0"
    )

@app.get("/")
async def root():
    """الصفحة الرئيسية"""
    return {
        "message": "Video Download Server",
        "version": "1.0.0",
        "endpoints": {
            "ping": "/ping",
            "download": "/download",
            "status": "/status/{request_id}"
        }
    }

@app.post("/download", response_model=VideoResponse)
async def download_video(
    video_request: VideoRequest,
    request: Request,
    background_tasks: BackgroundTasks
):
    """معالجة طلب تحميل الفيديو"""
    
    # استخراج معلومات العميل
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")
    
    # إنشاء معرف فريد للطلب
    request_id = str(uuid.uuid4())
    
    try:
        # حفظ الطلب في قاعدة البيانات (غير متزامن)
        background_tasks.add_task(
            save_request_to_db,
            request_id,
            video_request,
            client_ip,
            user_agent
        )
        
        # معالجة الفيديو (متزامن)
        response = process_video_request(video_request)
        
        # تحديث قاعدة البيانات (غير متزامن)
        background_tasks.add_task(
            update_request_in_db,
            request_id,
            response
        )
        
        return response
        
    except Exception as e:
        logger.error(f"Error in download endpoint: {e}")
        error_response = VideoResponse(
            status="error",
            video_id=request_id,
            error=str(e)
        )
        
        # حفظ الخطأ في قاعدة البيانات
        background_tasks.add_task(
            update_request_in_db,
            request_id,
            error_response
        )
        
        return error_response

@app.get("/status/{request_id}")
async def get_status(request_id: str):
    """الحصول على حالة طلب معين"""
    request_data = await get_request_from_db(request_id)
    
    if not request_data:
        raise HTTPException(status_code=404, detail="Request not found")
    
    return request_data

@app.get("/requests")
async def get_recent_requests(limit: int = Query(10, ge=1, le=100)):
    """الحصول على أحدث الطلبات"""
    try:
        async with aiosqlite.connect(DB_FILE) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute('''
                SELECT id, url, format_type, quality, status, 
                       title, created_at, updated_at
                FROM requests 
                ORDER BY created_at DESC 
                LIMIT ?
            ''', (limit,))
            
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"Error getting recent requests: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/stats")
async def get_stats():
    """الحصول على إحصائيات السيرفر"""
    try:
        async with aiosqlite.connect(DB_FILE) as db:
            # إحصائيات الطلبات
            cursor = await db.execute('''
                SELECT 
                    COUNT(*) as total_requests,
                    SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as successful,
                    SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as failed,
                    SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending
                FROM requests
            ''')
            stats = await cursor.fetchone()
            
            # أكثر المواقع طلباً
            cursor = await db.execute('''
                SELECT 
                    CASE 
                        WHEN url LIKE '%youtube.com%' OR url LIKE '%youtu.be%' THEN 'YouTube'
                        WHEN url LIKE '%tiktok.com%' THEN 'TikTok'
                        WHEN url LIKE '%twitter.com%' OR url LIKE '%x.com%' THEN 'Twitter'
                        WHEN url LIKE '%instagram.com%' THEN 'Instagram'
                        WHEN url LIKE '%facebook.com%' THEN 'Facebook'
                        ELSE 'Other'
                    END as platform,
                    COUNT(*) as count
                FROM requests
                GROUP BY platform
                ORDER BY count DESC
            ''')
            platforms = await cursor.fetchall()
            
            return {
                "total_requests": stats[0],
                "successful": stats[1],
                "failed": stats[2],
                "pending": stats[3],
                "platforms": [{"platform": p[0], "count": p[1]} for p in platforms]
            }
    except Exception as e:
        logger.error(f"Error getting stats: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

# تهيئة قاعدة البيانات عند بدء التشغيل
@app.on_event("startup")
async def startup_event():
    """تهيئة قاعدة البيانات عند بدء التشغيل"""
    init_db()
    logger.info("Server started successfully")

# نقط النهاية للصحة
@app.get("/health")
async def health_check():
    """فحص صحة السيرفر"""
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}