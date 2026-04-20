"""
Activation Manager Module - Professional Grade
Handles: Auto-Sniper, SMS Polling, Dynamic Status Management, and Session Recovery.
Optimized for: Shared hosting and high-concurrency environments.
"""

import asyncio
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime

# استيراد الكلاسات والأخطاء من ملف sms_client.py
from sms_client import (
    GrizzlyClient, 
    AliSMSClient, 
    NoNumbersError, 
    NoBalanceError, 
    SMSAPIError,
    NumberResponse,
    StatusResponse
)

logger = logging.getLogger("ActivationManager")

class ActivationManager:
    def __init__(self, grizzly_key: str, ali_key: str, db_connection):
        """
        تهيئة مدير التفعيلات.
        :param grizzly_key: مفتاح API لموقع Grizzly
        :param ali_key: مفتاح API لموقع AliSMS
        :param db_connection: كائن قاعدة البيانات من ملف database.py
        """
        self.grizzly = GrizzlyClient(grizzly_key)
        self.ali = AliSMSClient(ali_key)
        self.db = db_connection
        
        # لتتبع مهام الصياد الآلي النشطة (لمنع التكرار وإمكانية الإيقاف)
        self.active_snipers: Dict[str, asyncio.Task] = {}
        # لتتبع مهام انتظار الرسائل (Polling)
        self.active_polling: Dict[str, asyncio.Task] = {}

    # ==========================================
    # 🎯 نظام الصياد الآلي (Auto-Sniper)
    # ==========================================
    async def start_sniper(self, user_id: str, service: str, country: str, operator: Optional[str] = None, provider: str = "grizzly"):
        """بدء عملية الصيد في مهمة منفصلة بناءً على المزود المحدد"""
        sniper_id = f"sniper_{user_id}_{service}_{country}"
        
        # إذا كان هناك صياد يعمل لنفس الطلب، نقوم بإلغائه أولاً
        if sniper_id in self.active_snipers:
            self.active_snipers[sniper_id].cancel()
            
        task = asyncio.create_task(self._sniper_worker(user_id, service, country, operator, sniper_id, provider))
        self.active_snipers[sniper_id] = task

    async def _sniper_worker(self, user_id: str, service: str, country: str, operator: Optional[str], sniper_id: str, provider: str):
        """العامل الذي يحاول شراء الرقم بشكل متكرر من المزود المطلوب حتى النجاح"""
        logger.info(f"Sniper started for user {user_id} - Service: {service} - Provider: {provider}")
        
        # تحديد المزود بناءً على اختيار المستخدم
        client = self.grizzly if provider == "grizzly" else self.ali
        
        attempt = 0
        while True:
            try:
                attempt += 1
                
                # استخدام المزود الصحيح لطلب الرقم
                number_data: NumberResponse = await client.get_number(service=service, country=country, operator=operator)
                
                # إذا نجح الصيد:
                logger.info(f"🎯 Sniper Success! Number: {number_data.phone_number} from {provider.upper()}")
                
                # 1. حفظ في قاعدة البيانات (مع تمرير المزود الصحيح)
                await self.db.add_activation(
                    act_id=number_data.activation_id,
                    phone=number_data.phone_number,
                    service=service,
                    country=country,
                    provider=provider, 
                    user_id=user_id
                )
                
                # 2. بدء مراقبة الرسائل فوراً لهذا الرقم
                self.start_sms_polling(number_data.activation_id, provider)
                
                # 3. إيقاف الصياد
                break

            except (NoNumbersError, NoBalanceError) as e:
                # في حال عدم توفر أرقام أو رصيد، ننتظر قليلاً ثم نحاول مرة أخرى
                wait_time = 5 if attempt < 10 else 15
                await asyncio.sleep(wait_time)
            except asyncio.CancelledError:
                logger.info(f"Sniper {sniper_id} was stopped by user.")
                break
            except Exception as e:
                logger.error(f"Sniper Error ({provider}): {e}")
                await asyncio.sleep(10)
        
        # تنظيف القائمة بعد الانتهاء
        self.active_snipers.pop(sniper_id, None)

    # ==========================================
    # 📩 نظام مراقبة الرسائل (SMS Polling)
    # ==========================================
    def start_sms_polling(self, activation_id: str, provider: str):
        """بدء مهمة مراقبة الكود لرقم معين"""
        if activation_id in self.active_polling:
            return
            
        task = asyncio.create_task(self._poll_sms_worker(activation_id, provider))
        self.active_polling[activation_id] = task

    async def _poll_sms_worker(self, activation_id: str, provider: str):
        """العامل الذي يفحص حالة التفعيل كل بضع ثوانٍ"""
        client = self.grizzly if provider == "grizzly" else self.ali
        start_time = datetime.now()
        
        try:
            while True:
                # التحقق من مرور وقت طويل (مثلاً 15 دقيقة) لإلغاء المراقبة تلقائياً
                if (datetime.now() - start_time).total_seconds() > 900:
                    logger.info(f"Polling timeout for {activation_id}. Cancelling session.")
                    break
                
                status_data: StatusResponse = await client.get_status(activation_id)
                
                if status_data.status_code == "OK":
                    # تم استلام الكود!
                    await self.db.update_sms_info(
                        activation_id, 
                        status_data.activation_code, 
                        status_data.sms_text
                    )
                    logger.info(f"✅ SMS Received for {activation_id} ({provider})")
                    break # توقف عن المراقبة
                
                elif status_data.status_code == "CANCELLED":
                    await self.db.update_status(activation_id, "CANCELLED")
                    logger.info(f"🚫 Activation {activation_id} was cancelled by {provider}.")
                    break
                
                await asyncio.sleep(5) # فحص كل 5 ثوانٍ
                
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Polling error for {activation_id} ({provider}): {e}")
        finally:
            self.active_polling.pop(activation_id, None)

    # ==========================================
    # ⚙️ عمليات التحكم (Actions)
    # ==========================================
    async def cancel_number(self, activation_id: str, provider: str) -> bool:
        """إلغاء الرقم واسترداد الرصيد (Status = 8)"""
        client = self.grizzly if provider == "grizzly" else self.ali
        
        # الأغلبية تستخدم 8 للإلغاء، وبعضها يستخدم -1. سنجرب 8 ثم -1 إذا لزم الأمر
        success = await client.set_status(activation_id, 8)
        if not success:
            success = await client.set_status(activation_id, -1)
            
        if success:
            await self.db.update_status(activation_id, "CANCELLED")
            if activation_id in self.active_polling:
                self.active_polling[activation_id].cancel()
        return success

    async def ban_number(self, activation_id: str, provider: str) -> bool:
        """حظر الرقم (Status = 8)"""
        client = self.grizzly if provider == "grizzly" else self.ali
        success = await client.set_status(activation_id, 8)
        if success:
            await self.db.update_status(activation_id, "BANNED")
            if activation_id in self.active_polling:
                self.active_polling[activation_id].cancel()
        return success

    async def finish_activation(self, activation_id: str, provider: str) -> bool:
        """إنهاء التفعيل بنجاح (Status = 6)"""
        client = self.grizzly if provider == "grizzly" else self.ali
        success = await client.set_status(activation_id, 6)
        if success:
            await self.db.update_status(activation_id, "COMPLETED")
        return success

    # ==========================================
    # 🔄 استعادة الجلسات (State Persistence)
    # ==========================================
    async def restore_sessions(self):
        """
        تعمل عند تشغيل البوت: تجلب الأرقام التي لا تزال 'تنتظر' وتبدأ مراقبتها مجدداً.
        """
        active_acts = await self.db.get_all_active()
        for act in active_acts:
            self.start_sms_polling(act['activation_id'], act['provider'])
        logger.info(f"Restored {len(active_acts)} active polling sessions.")
