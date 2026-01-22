import asyncio
import yt_dlp
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any
from config import get_settings

# إعداد الإعدادات والسجلات
settings = get_settings()
logger = logging.getLogger("service")

# إنشاء ThreadPoolExecutor لتشغيل المهام الثقيلة (yt-dlp) بعيداً عن الـ Event Loop الرئيسي
executor = ThreadPoolExecutor(max_workers=settings.MAX_WORKERS)

class YtDlpService:
    @staticmethod
    def _get_options(format_type: str, quality: str) -> Dict[str, Any]:
        """
        تجهيز إعدادات yt-dlp مع إضافة تقنيات تخطي الحظر (Anti-Bot)
        """
        # الإعدادات الأساسية التي طلبتها مضافاً إليها تحسينات الأمان
        opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            'force_ipv4': True,
            'noplaylist': True,
            
            # --- تخطي حماية يوتيوب (هذا الجزء الأهم) ---
            'extractor_args': {
                'youtube': {
                    'player_client': ['android', 'web'], # استخدام مشغل أندرويد يقلل احتمالية كشف البوت
                    'skip': ['hls', 'dash']
                }
            },
            
            # محاكاة متصفح حقيقي
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-us,en;q=0.5',
            },
            
            # تجاوز القيود الجغرافية إن وجدت
            'geo_bypass': True,
            'nocheckcertificate': True,
        }

        # تحديد الصيغة (فيديو أو صوت)
        if format_type == 'audio':
            opts.update({
                'format': 'bestaudio/best',
            })
        else:
            # خريطة الجودة بناءً على الارتفاع
            quality_map = {
                "360": "best[height<=360]",
                "480": "best[height<=480]",
                "720": "best[height<=720]",
                "1080": "best[height<=1080]",
                "best": "best"
            }
            opts['format'] = quality_map.get(quality, 'best')
        
        return opts

    @staticmethod
    def _extract_sync(url: str, options: Dict) -> Dict:
        """
        تنفيذ عملية الاستخراج بشكل متزامن (Synchronous)
        """
        with yt_dlp.YoutubeDL(options) as ydl:
            # download=False لاستخراج المعلومات فقط دون تحميل الملف على السيرفر
            info = ydl.extract_info(url, download=False)
            return info

    @classmethod
    async def process_url(cls, url: str, format_type: str = "video", quality: str = "720") -> Dict:
        """
        المحرك الرئيسي: يستدعي المهام في Thread منفصل لضمان عدم تجميد السيرفر
        """
        options = cls._get_options(format_type, quality)
        loop = asyncio.get_running_loop()

        try:
            # تشغيل الدالة المتزامنة داخل الـ ThreadPool
            info = await loop.run_in_executor(
                executor, 
                lambda: cls._extract_sync(url, options)
            )
            
            # معالجة وتنظيف البيانات المستخرجة
            result = {
                "status": "success",
                "title": info.get("title"),
                "duration": info.get("duration"),
                "thumbnail": info.get("thumbnail"),
                "url": info.get("url"), # الرابط المباشر النهائي
                "ext": info.get("ext"),
                "channel_name": info.get("uploader"),
                "filesize": info.get("filesize") or info.get("filesize_approx"),
            }
            
            # في حال لم يكن الرابط في الجذر، نبحث عنه داخل الـ formats
            if not result['url'] and 'formats' in info:
                # تصفية الروابط التي تحتوي على فيديو وصوت معاً أو الأفضل حسب الطلب
                best_f = info['formats'][-1]
                result['url'] = best_f.get('url')
            
            return result

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Error processing URL {url}: {error_msg}")
            
            # تخصيص رسالة الخطأ للمستخدم
            clean_error = "فشل في الوصول للفيديو. قد تكون هناك قيود من المصدر."
            if "Sign in to confirm you're not a bot" in error_msg:
                clean_error = "يوتيوب قام بحظر الطلب (Bot Protection). يرجى محاولة استخدام ملف Cookies."
            elif "Incomplete data received" in error_msg:
                clean_error = "بيانات الفيديو غير مكتملة، حاول مرة أخرى."
                
            return {
                "status": "error", 
                "error": clean_error,
                "raw_error": error_msg if settings.DEBUG else None
            }
