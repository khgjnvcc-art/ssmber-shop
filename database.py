"""
Database Management Module - Production Grade
يتعامل مع قاعدة بيانات SQLite باستخدام Async Wrappers.
يحتوي على نظام WAL (Write-Ahead Logging) لدعم الـ Concurrency العالي 
ومنع أخطاء "database is locked".
"""

import sqlite3
import asyncio
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger("Database")

class Database:
    def __init__(self, db_path: str = "sms_bot.db"):
        """
        تهيئة مدير قاعدة البيانات.
        :param db_path: مسار ملف قاعدة البيانات
        """
        self.db_path = db_path
        self._init_db()

    # ==========================================
    # ⚙️ محرك التنفيذ الأساسي (Sync Thread-Safe Core)
    # ==========================================
    def _execute_sync(self, query: str, params: tuple = (), fetch: str = "none") -> Any:
        """
        دالة متزامنة (Sync) آمنة لتنفيذ استعلامات SQLite في Thread منفصل.
        """
        try:
            # استخدام timeout=10.0 لتجنب الأخطاء عند الضغط العالي
            with sqlite3.connect(self.db_path, check_same_thread=False, timeout=10.0) as conn:
                # إرجاع النتائج كـ القواميس (Dictionaries) لسهولة التعامل معها في الـ Code
                conn.row_factory = sqlite3.Row
                
                # تفعيل وضع WAL لتسريع عمليات الكتابة والقراءة المتزامنة
                conn.execute('PRAGMA journal_mode=WAL;')
                
                cursor = conn.cursor()
                cursor.execute(query, params)
                
                if fetch == "one":
                    result = cursor.fetchone()
                    return dict(result) if result else None
                elif fetch == "all":
                    results = cursor.fetchall()
                    return [dict(row) for row in results]
                else:
                    conn.commit()
                    return cursor.rowcount
                    
        except sqlite3.Error as e:
            logger.error(f"SQLite Error: {e} | Query: {query} | Params: {params}")
            raise

    # ==========================================
    # 🔄 الغلاف غير المتزامن (Async Wrappers)
    # ==========================================
    async def _execute_async(self, query: str, params: tuple = (), fetch: str = "none") -> Any:
        """
        تحويل التنفيذ المتزامن إلى غير متزامن (Async) لعدم تجميد البوت.
        """
        return await asyncio.to_thread(self._execute_sync, query, params, fetch)

    # ==========================================
    # 🏗️ بناء وتهيئة الجداول (Initialization)
    # ==========================================
    def _init_db(self):
        """إنشاء الجداول والفهارس (Indexes) اللازمة"""
        query = '''
        CREATE TABLE IF NOT EXISTS activations (
            activation_id TEXT PRIMARY KEY,
            phone_number TEXT NOT NULL,
            service TEXT NOT NULL,
            country TEXT NOT NULL,
            provider TEXT NOT NULL,
            user_id TEXT NOT NULL,
            status TEXT DEFAULT 'WAITING',
            code TEXT,
            sms_text TEXT,
            notified INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        '''
        self._execute_sync(query)
        
        # إنشاء فهارس (Indexes) احترافية لتسريع البحث (Performance Optimization)
        self._execute_sync('CREATE INDEX IF NOT EXISTS idx_status ON activations(status)')
        self._execute_sync('CREATE INDEX IF NOT EXISTS idx_user_status ON activations(user_id, status)')
        # فهرس جزئي لتسريع وظيفة الـ Background Notifier
        self._execute_sync('CREATE INDEX IF NOT EXISTS idx_unnotified ON activations(notified) WHERE code IS NOT NULL')
        
        logger.info("Database initialized successfully with WAL mode and Indexes.")

    # ==========================================
    # 📝 عمليات الإدخال والتحديث (Write Operations)
    # ==========================================
    async def add_activation(self, act_id: str, phone: str, service: str, country: str, provider: str, user_id: str):
        """إضافة تفعيل جديد إلى قاعدة البيانات"""
        query = '''
        INSERT INTO activations (activation_id, phone_number, service, country, provider, user_id)
        VALUES (?, ?, ?, ?, ?, ?)
        '''
        await self._execute_async(query, (act_id, phone, service, country, provider, user_id))

    async def update_status(self, act_id: str, status: str):
        """تحديث حالة التفعيل (مثال: CANCELLED, BANNED, COMPLETED)"""
        query = 'UPDATE activations SET status = ? WHERE activation_id = ?'
        await self._execute_async(query, (status, act_id))

    async def update_sms_info(self, act_id: str, code: Optional[str], sms_text: Optional[str]):
        """حفظ كود التفعيل المستلم والنص وتغيير الحالة لـ COMPLETED"""
        query = '''
        UPDATE activations 
        SET code = ?, sms_text = ?, status = 'COMPLETED' 
        WHERE activation_id = ?
        '''
        await self._execute_async(query, (code, sms_text, act_id))

    async def mark_as_notified(self, act_id: str):
        """تحديث حالة الإشعار بعد إرسال الكود للمستخدم في تليجرام"""
        query = 'UPDATE activations SET notified = 1 WHERE activation_id = ?'
        await self._execute_async(query, (act_id,))

    # ==========================================
    # 🔍 عمليات القراءة والاسترجاع (Read Operations)
    # ==========================================
    async def get_all_active(self) -> List[Dict[str, Any]]:
        """
        جلب جميع التفعيلات النشطة (لغرض استعادة الجلسات عند الريستارت).
        تستخدم في: ActivationManager.restore_sessions
        """
        query = "SELECT activation_id, provider FROM activations WHERE status = 'WAITING'"
        return await self._execute_async(query, fetch="all")

    async def get_user_activations(self, user_id: str, status: str = "WAITING") -> List[Dict[str, Any]]:
        """
        جلب تفعيلات مستخدم معين بناءً على الحالة.
        تستخدم في: main.py (القائمة النشطة)
        """
        query = '''
        SELECT activation_id, provider, phone_number, service 
        FROM activations 
        WHERE user_id = ? AND status = ?
        ORDER BY created_at DESC
        '''
        return await self._execute_async(query, (user_id, status), fetch="all")

    async def get_unnotified_completed_activations(self) -> List[Dict[str, Any]]:
        """
        جلب التفعيلات المكتملة التي لم يتم إرسال إشعار بها للمستخدم بعد.
        تستخدم في: main.py (Notification Worker)
        """
        query = '''
        SELECT activation_id, user_id, phone_number, code, sms_text 
        FROM activations 
        WHERE code IS NOT NULL AND notified = 0
        '''
        return await self._execute_async(query, fetch="all")

