import asyncio
import yt_dlp
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any, Optional
from config import get_settings

settings = get_settings()
logger = logging.getLogger("service")

# إنشاء ThreadPoolExecutor لمهام التحميل الثقيلة
executor = ThreadPoolExecutor(max_workers=settings.MAX_WORKERS)

class YtDlpService:
    @staticmethod
    def _get_options(format_type: str, quality: str) -> Dict[str, Any]:
        """تجهيز إعدادات yt-dlp"""
        opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            # استخدام IPv4 لتقليل مشاكل الاتصال في بعض البيئات
            'force_ipv4': True,
            # عدم تحميل قوائم التشغيل
            'noplaylist': True,
            # User Agent وهمي لتجنب الحظر
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        }

        if format_type == 'audio':
            opts.update({
                'format': 'bestaudio/best',
                # لا نحتاج postprocessors هنا لأننا فقط نستخرج الرابط، 
                # التحويل يحتاج تحميل الملف فعلياً على السيرفر وهو مكلف
            })
        else:
            # خريطة الجودة
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
        """الدالة المتزامنة التي سيتم تشغيلها في Thread منفصل"""
        with yt_dlp.YoutubeDL(options) as ydl:
            # download=False مهم جداً لأننا نريد المعلومات فقط
            info = ydl.extract_info(url, download=False)
            return info

    @classmethod
    async def process_url(cls, url: str, format_type: str = "video", quality: str = "720") -> Dict:
        """
        معالجة الرابط بشكل غير متزامن
        """
        options = cls._get_options(format_type, quality)
        loop = asyncio.get_running_loop()

        try:
            # تشغيل العملية الثقيلة في Thread منفصل لعدم تجميد السيرفر
            info = await loop.run_in_executor(
                executor, 
                lambda: cls._extract_sync(url, options)
            )
            
            # معالجة النتائج
            result = {
                "status": "success",
                "title": info.get("title"),
                "duration": info.get("duration"),
                "thumbnail": info.get("thumbnail"),
                "url": info.get("url"), # الرابط المباشر
                "ext": info.get("ext"),
                "filesize": info.get("filesize") or info.get("filesize_approx"),
            }
            
            # في حالة وجود formats متعددة، نحاول التقاط الرابط الأفضل يدوياً إذا لم يرجعه extract_info
            if not result['url'] and 'formats' in info:
                # نأخذ آخر عنصر عادة ما يكون الأفضل
                best_format = info['formats'][-1]
                result['url'] = best_format.get('url')
            
            return result

        except Exception as e:
            logger.error(f"Error processing URL {url}: {str(e)}")
            return {
                "status": "error", 
                "error": str(e).split(';')[0] # اختصار رسالة الخطأ
            }
