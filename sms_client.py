"""
SMS API Clients Module - Production Grade
يدعم الاتصال بمزودي خدمات الـ SMS (GrizzlySMS & AliSMS) باستخدام aiohttp.
مدمج مع نظام استخراج الأكواد الذكي وإدارة أخطاء الشبكة والـ API.
"""

import aiohttp
import asyncio
import ssl
import certifi
import logging
import re
from dataclasses import dataclass
from typing import Optional, Dict, Any, List, Union

logger = logging.getLogger("SMSClient")

# ==========================================
# 🛑 تعريف الأخطاء المخصصة (Custom Exceptions)
# ==========================================
class SMSAPIError(Exception):
    """خطأ عام في الـ API"""
    pass

class NoNumbersError(SMSAPIError):
    """لا توجد أرقام متاحة حالياً"""
    pass

class NoBalanceError(SMSAPIError):
    """الرصيد غير كافٍ"""
    pass

class BadKeyError(SMSAPIError):
    """مفتاح API غير صالح"""
    pass

class ServiceUnavailableRegionError(SMSAPIError):
    """الخدمة غير متاحة في منطقتك (تحتاج لتغيير الـ IP)"""
    pass

class NoActivationError(SMSAPIError):
    """معرف التفعيل غير موجود"""
    pass

# ==========================================
# 📦 حاويات البيانات (Data Models)
# ==========================================
@dataclass
class NumberResponse:
    activation_id: str
    phone_number: str

@dataclass
class StatusResponse:
    status_code: str  # مثل "OK", "WAIT", "CANCELLED"
    activation_code: Optional[str] = None
    sms_text: Optional[str] = None

# ==========================================
# ⚙️ الكلاس الأساسي (Base HTTP Client)
# ==========================================
class SMSClientBase:
    """الكلاس الأساسي للاتصال بـ APIs الخاصة بمزودي الأرقام باستخدام Async"""
    
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TelesoonBot/2.0",
        "Accept": "application/json, text/plain, */*"
    }

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url
        self.api_key = api_key
        self._session: Optional[aiohttp.ClientSession] = None
        self._ssl_context = ssl.create_default_context(cafile=certifi.where())

    async def _get_session(self) -> aiohttp.ClientSession:
        """تهيئة أو استرجاع الجلسة الحالية لضمان استخدام Session واحدة فقط"""
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(ssl=self._ssl_context)
            self._session = aiohttp.ClientSession(
                connector=connector,
                headers=self.HEADERS
            )
        return self._session

    async def close(self):
        """إغلاق الجلسة بشكل آمن عند إيقاف البوت"""
        if self._session and not self._session.closed:
            await self._session.close()

    async def _request(self, action: str, params: Dict[str, Any] = None, is_json: bool = False) -> Union[str, Dict]:
        """دالة مركزية لإرسال الطلبات ومعالجة الأخطاء الشائعة"""
        session = await self._get_session()
        payload = {"api_key": self.api_key, "action": action}
        if params:
            payload.update(params)

        try:
            async with session.get(self.base_url, params=payload, timeout=aiohttp.ClientTimeout(total=15)) as response:
                response.raise_for_status()
                
                if is_json:
                    try:
                        data = await response.json(content_type=None)
                        return data
                    except Exception as e:
                        # في حال فشل تحويل JSON نرجع النص للتحقق من الأخطاء
                        text = await response.text()
                        self._check_common_errors(text)
                        raise SMSAPIError(f"فشل في تحليل JSON: {e}")
                else:
                    text = await response.text()
                    self._check_common_errors(text)
                    return text.strip()
                    
        except asyncio.TimeoutError:
            logger.error(f"Timeout while connecting to {self.base_url} (Action: {action})")
            raise SMSAPIError("انتهى وقت الاتصال بالخادم (Timeout).")
        except aiohttp.ClientError as e:
            logger.error(f"HTTP Error: {e}")
            raise SMSAPIError(f"خطأ في الاتصال: {e}")

    def _check_common_errors(self, response_text: str):
        """فحص الأخطاء القياسية لبروتوكول SMS-Hub الموحد"""
        res = response_text.strip().upper()
        if res == "NO_NUMBERS":
            raise NoNumbersError("لا توجد أرقام متاحة لهذه الخدمة والدولة حالياً.")
        elif res == "NO_BALANCE":
            raise NoBalanceError("رصيدك غير كافٍ لإتمام العملية.")
        elif res == "BAD_KEY":
            raise BadKeyError("مفتاح ה-API غير صالح.")
        elif res == "SERVICE_UNAVAILABLE_REGION":
            raise ServiceUnavailableRegionError("الخدمة محجوبة من منطقتك.")
        elif res == "NO_ACTIVATION":
            raise NoActivationError("معرف التفعيل غير موجود أو منتهي الصلاحية.")
        elif "ERROR" in res or "BAD_" in res:
            raise SMSAPIError(f"خطأ غير متوقع من الخادم: {res}")

    @staticmethod
    def extract_code_from_text(text: str) -> Optional[str]:
        """استخراج كود التفعيل من الرسالة النصية باستخدام Regex"""
        if not text:
            return None
        match = re.search(r'\b\d{4,8}\b', text)
        return match.group(0) if match else text

    # 🟢 دالة جلب المشغلين والأسعار (تمت إضافتها وتعمل مع كلا الموقعين)
    async def get_operators(self, country: str, service: str) -> List[Dict[str, Any]]:
        """جلب المشغلين والأسعار الحية للخدمة والدولة المحددة"""
        params = {"country": country, "service": service}
        try:
            # معظم مواقع SMS-Hub ترجع استجابة JSON عند طلب getPrices
            res = await self._request("getPrices", params=params, is_json=True)
            operators_list = []
            
            # صيغة الاستجابة القياسية: {"country_id": {"service_id": {"operator_name": {"count": 100, "price": 0.2}}}}
            country_data = res.get(str(country), {})
            service_data = country_data.get(service, {})
            
            for op_name, op_info in service_data.items():
                # بعض الـ APIs ترجع السعر كـ string وبعضها float
                price = op_info.get("cost", op_info.get("price", "?"))
                count = op_info.get("count", 0)
                
                # إضافة المشغل العشوائي (any) أو المشغلين الذين لديهم أرقام
                if int(count) >= 0 or op_name == "any":
                    op_display_name = "تلقائي / أسرع مشغل" if op_name in ["any", ""] else op_name.capitalize()
                    op_id = "any" if op_name in ["any", ""] else op_name
                    
                    operators_list.append({
                        "id": op_id,
                        "name": op_display_name,
                        "price": str(price),
                        "count": f"+{count}" if int(count) > 0 else str(count)
                    })
            
            # ترتيب القائمة ليكون "التلقائي" هو الأول
            operators_list.sort(key=lambda x: 0 if x["id"] == "any" else 1)
            
            # إذا لم يتم العثور على مشغلين، نضع خيار احتياطي
            if not operators_list:
                operators_list.append({"id": "any", "name": "تلقائي (البيانات غير متوفرة)", "price": "?", "count": "0"})
                
            return operators_list

        except Exception as e:
            logger.error(f"Error fetching operators for {service} in {country}: {e}")
            # خيار افتراضي في حال فشل جلب البيانات الحية حتى لا يتوقف البوت
            return [{"id": "any", "name": "تلقائي (حدث خطأ في الجلب)", "price": "Auto", "count": "+"}]


# ==========================================
# 🐻 مزود الخدمة: GrizzlySMS
# ==========================================
class GrizzlyClient(SMSClientBase):
    """عميل الاتصال بـ GrizzlySMS"""
    def __init__(self, api_key: str):
        super().__init__(base_url="https://api.grizzlysms.com/stubs/handler_api.php", api_key=api_key)

    async def get_balance(self) -> str:
        res = await self._request("getBalance")
        if res.startswith("ACCESS_BALANCE:"):
            return res.split(":")[1]
        raise SMSAPIError(f"استجابة غير متوقعة للرصيد: {res}")

    async def get_number(self, service: str, country: str = "any", operator: Optional[str] = None, max_price: Optional[str] = None) -> NumberResponse:
        params = {"service": service, "country": country}
        if operator and operator != "any":
            params["operator"] = operator
        if max_price:
            params["maxPrice"] = max_price

        res = await self._request("getNumber", params=params)
        
        if res.startswith("ACCESS_NUMBER:"):
            parts = res.split(":")
            if len(parts) >= 3:
                return NumberResponse(activation_id=parts[1], phone_number=parts[2])
                
        raise SMSAPIError(f"استجابة غير صالحة من GrizzlySMS عند طلب الرقم: {res}")

    async def get_status(self, activation_id: str) -> StatusResponse:
        params = {"id": activation_id}
        res = await self._request("getStatus", params=params)
        res_upper = res.upper()

        if res_upper.startswith("STATUS_OK:"):
            code_part = res.split(":", 1)[1]
            extracted_code = self.extract_code_from_text(code_part)
            return StatusResponse(status_code="OK", activation_code=extracted_code, sms_text=code_part)
            
        elif res_upper in ["STATUS_WAIT_CODE", "STATUS_WAIT_RESEND"] or res_upper.startswith("STATUS_WAIT_RETRY"):
            return StatusResponse(status_code="WAIT")
            
        elif res_upper == "STATUS_CANCEL":
            return StatusResponse(status_code="CANCELLED")
            
        raise SMSAPIError(f"استجابة حالة غير معروفة: {res}")

    async def set_status(self, activation_id: str, status: int) -> bool:
        params = {"id": activation_id, "status": str(status)}
        res = await self._request("setStatus", params=params)
        
        expected_responses = {
            "-1": "ACCESS_CANCEL",
            "1": "ACCESS_READY",
            "3": "ACCESS_RETRY_GET",
            "6": "ACCESS_ACTIVATION",
            "8": "ACCESS_CANCEL"
        }
        
        if res.upper() == expected_responses.get(str(status), "") or res.upper() == "ACCESS_CANCEL":
            return True
            
        logger.warning(f"Set status expected match failed. ID: {activation_id}, API Response: {res}")
        return False

# ==========================================
# 🟠 مزود الخدمة: AliSMS
# ==========================================
class AliSMSClient(SMSClientBase):
    """عميل الاتصال بـ AliSMS"""
    def __init__(self, api_key: str):
        super().__init__(base_url="https://api.alisms.org/stubs/handler_api.php", api_key=api_key)

    async def get_balance(self) -> str:
        res = await self._request("getBalance")
        if res.startswith("ACCESS_BALANCE:"):
            return res.split(":")[1]
        raise SMSAPIError(f"استجابة غير متوقعة للرصيد من AliSMS: {res}")

    async def get_number(self, service: str, country: str = "any", operator: Optional[str] = None, max_price: Optional[str] = None) -> NumberResponse:
        params = {"service": service, "country": country}
        if operator and operator != "any":
            params["operator"] = operator
        if max_price:
            params["maxPrice"] = max_price

        res = await self._request("getNumber", params=params)
        
        if res.startswith("ACCESS_NUMBER:"):
            parts = res.split(":")
            if len(parts) >= 3:
                return NumberResponse(activation_id=parts[1], phone_number=parts[2])
                
        raise SMSAPIError(f"استجابة غير صالحة من AliSMS عند طلب الرقم: {res}")

    async def get_status(self, activation_id: str) -> StatusResponse:
        params = {"id": activation_id}
        res = await self._request("getStatus", params=params)
        res_upper = res.upper()

        if res_upper.startswith("STATUS_OK:"):
            code_part = res.split(":", 1)[1]
            extracted_code = self.extract_code_from_text(code_part)
            return StatusResponse(status_code="OK", activation_code=extracted_code, sms_text=code_part)
            
        elif res_upper in ["STATUS_WAIT_CODE", "STATUS_WAIT_RESEND"] or res_upper.startswith("STATUS_WAIT_RETRY"):
            return StatusResponse(status_code="WAIT")
            
        elif res_upper == "STATUS_CANCEL":
            return StatusResponse(status_code="CANCELLED")
            
        raise SMSAPIError(f"استجابة حالة غير معروفة من AliSMS: {res}")

    async def set_status(self, activation_id: str, status: int) -> bool:
        params = {"id": activation_id, "status": str(status)}
        res = await self._request("setStatus", params=params)
        
        if "ACCESS" in res.upper():
            return True
            
        logger.warning(f"AliSMS Set status failed. ID: {activation_id}, API Response: {res}")
        return False
