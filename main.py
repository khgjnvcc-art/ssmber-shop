import os
import asyncio
import logging
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramAPIError

# استيراد المكونات المحلية
from database import Database
from activation_manager import ActivationManager
from sms_client import SMSAPIError

# ==========================================
# ⚙️ الإعدادات الأساسية
# ==========================================
API_TOKEN = "8033899165:AAHwx7_lIDxXLcPxyG0HqhQwg6FtY9u3TW8"
GRIZZLY_KEY = "0fee820164b18c68456a3f6197eb5900"
ALI_KEY = "FM37hEbOKzTifWNjtEsLefhNzM8p9duuRyWRmoBvZSlgyJUGNv"

logging.basicConfig(
    level=logging.INFO, 
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("TelesoonV2")

# تهيئة الكائنات
db = Database()
manager = ActivationManager(GRIZZLY_KEY, ALI_KEY, db)
bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ==========================================
# 🧠 إدارة الحالات (FSM States)
# ==========================================
class BotFlow(StatesGroup):
    waiting_for_country = State()
    waiting_for_service = State()

# ==========================================
# 🏠 القائمة الرئيسية والواجهة (UI/UX)
# ==========================================
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await show_main_menu(message)

async def show_main_menu(message_or_callback):
    builder = InlineKeyboardBuilder()
    builder.button(text="🛒 شراء رقم جديد", callback_data="menu_buy")
    builder.button(text="📋 التفعيلات النشطة", callback_data="menu_active")
    builder.button(text="💳 التحقق من الرصيد", callback_data="menu_balance")
    builder.adjust(1, 2)
    
    text = "🛠️ **نظام الصيد والتفعيلات الاحترافي**\n\nاختر العملية التي تود القيام بها:"
    
    if isinstance(message_or_callback, types.Message):
        await message_or_callback.answer(text, reply_markup=builder.as_markup(), parse_mode="Markdown")
    else:
        await message_or_callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="Markdown")

@dp.callback_query(F.data == "menu_main")
async def back_to_main(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await show_main_menu(callback)

# ==========================================
# 💳 إدارة الرصيد (Balance)
# ==========================================
@dp.callback_query(F.data == "menu_balance")
async def check_balance(callback: types.CallbackQuery):
    await callback.message.edit_text("⏳ جاري التحقق من الرصيد في السيرفرات...")
    
    try:
        grizzly_bal = await manager.grizzly.get_balance()
    except Exception as e:
        grizzly_bal = f"خطأ: {e}"
        
    try:
        ali_bal = await manager.ali.get_balance()
    except Exception as e:
        ali_bal = f"خطأ: {e}"

    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 رجوع", callback_data="menu_main")
    
    text = (
        "💰 **تفاصيل الرصيد الحالي:**\n\n"
        f"🐻 **GrizzlySMS:** `{grizzly_bal}$`\n"
        f"🟠 **AliSMS:** `{ali_bal}$`"
    )
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="Markdown")

# ==========================================
# 🛒 تدفق الشراء (Buy Flow) - اختيار المزود والدولة والخدمة
# ==========================================
@dp.callback_query(F.data == "menu_buy")
async def choose_provider(callback: types.CallbackQuery):
    builder = InlineKeyboardBuilder()
    builder.button(text="🐻 Grizzly SMS", callback_data="prov_grizzly")
    builder.button(text="🟠 AliSMS", callback_data="prov_alisms")
    builder.button(text="🔙 رجوع", callback_data="menu_main")
    builder.adjust(2, 1)
    
    await callback.message.edit_text("اختر مزود الخدمة لبدء العمل:", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("prov_"))
async def ask_country(callback: types.CallbackQuery, state: FSMContext):
    provider = callback.data.split("_")[1]
    await state.update_data(provider=provider)
    await state.set_state(BotFlow.waiting_for_country)
    
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 رجوع للمزودين", callback_data="menu_buy")
    
    await callback.message.edit_text(
        f"✅ تم اختيار: **{provider.upper()}**\n\n🔎 أرسل (اسم الدولة بالإنجليزية) أو (كود الدولة):\nمثال: `Egypt` أو `20`", 
        reply_markup=builder.as_markup(), parse_mode="Markdown"
    )

@dp.message(BotFlow.waiting_for_country)
async def handle_country_search(message: types.Message, state: FSMContext):
    query = message.text.lower()
    await state.update_data(country=query)
    await state.set_state(BotFlow.waiting_for_service)
    await message.answer(f"✅ تم تحديد الدولة: `{query}`\nالآن أرسل اسم الخدمة المطلوبة (مثال: `whatsapp`, `telegram`):", parse_mode="Markdown")

@dp.message(BotFlow.waiting_for_service)
async def handle_service_search(message: types.Message, state: FSMContext):
    service_query = message.text.lower()
    data = await state.get_data()
    provider = data.get('provider')
    country_id = data.get('country')
    
    msg = await message.answer("⏳ جاري جلب المشغلين والأسعار الحية من السيرفر...")
    
    client = manager.grizzly if provider == "grizzly" else manager.ali
    
    try:
        # هذه الدالة سيتم توحيدها في ملف sms_client.py القادم
        if hasattr(client, 'get_operators'):
            operators = await client.get_operators(country_id, service_query)
        else:
            # افتراضي مؤقت حتى يتم رفع التحديث لملف الـ API
            operators = [{"id": "any", "name": "أسرع مشغل (تلقائي)", "price": "0.20", "count": "+10"}]
        
        builder = InlineKeyboardBuilder()
        for op in operators:
            # هنا التعديل الجوهري: زر الصيد بجانب زر السعر واسم المشغل
            btn_snipe = types.InlineKeyboardButton(
                text="🎯 صيد حصري", 
                callback_data=f"snipe_{provider}_{service_query}_{country_id}_{op['id']}"
            )
            btn_info = types.InlineKeyboardButton(
                text=f"📡 {op['name']} | {op['price']}$", 
                callback_data="ignore_click"
            )
            # دمج الزرين في صف واحد
            builder.row(btn_snipe, btn_info)
        
        builder.row(types.InlineKeyboardButton(text="🔙 القائمة الرئيسية", callback_data="menu_main"))
        
        await msg.edit_text(
            f"📦 **المزود:** `{provider.upper()}`\n"
            f"🌍 **الدولة:** `{country_id}`\n"
            f"⚡ **الخدمة:** `{service_query.upper()}`\n\n"
            "اضغط على **'صيد حصري'** بجانب المشغل الذي يناسبك للسعر الموضح:", 
            reply_markup=builder.as_markup(), parse_mode="Markdown"
        )
                             
    except SMSAPIError as e:
        await msg.edit_text(f"❌ حدث خطأ أثناء الاتصال بالمزود: {str(e)}")

@dp.callback_query(F.data == "ignore_click")
async def ignore_info_click(callback: types.CallbackQuery):
    await callback.answer("هذا الزر لعرض السعر والمشغل فقط، اضغط على 'صيد حصري' للبدء.", show_alert=True)

# ==========================================
# 🎯 إطلاق الصياد (Auto-Sniper)
# ==========================================
@dp.callback_query(F.data.startswith("snipe_"))
async def start_exclusive_snipe(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    # snipe_provider_service_country_operator
    provider, service, country, operator = parts[1], parts[2], parts[3], parts[4]
    user_id = str(callback.from_user.id)
    operator = None if operator == "any" else operator
    
    await callback.answer("🚀 بدأ الصيد الذكي...", show_alert=False)
    
    # تمرير المزود (provider) ليعرف مدير التفعيلات أي موقع يستخدم
    await manager.start_sniper(user_id=user_id, service=service, country=country, operator=operator, provider=provider)
    
    builder = InlineKeyboardBuilder()
    builder.button(text="🛑 إيقاف الصيد", callback_data=f"stop_{user_id}_{service}_{country}")
    builder.button(text="📋 متابعة التفعيلات النشطة", callback_data="menu_active")
    builder.adjust(1)
    
    await callback.message.edit_text(
        f"🎯 **وضعية الصياد الآلي نشطة**\n\n"
        f"🌐 المزود: `{provider.upper()}`\n"
        f"🔹 الخدمة: `{service}`\n"
        f"🔹 الدولة: `{country}`\n\n"
        "البوت يراقب السيرفر الآن.. سيصلك إشعار فور صيد الرقم واستلام الكود.",
        reply_markup=builder.as_markup(),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data.startswith("stop_"))
async def stop_sniper(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    user_id, service, country = parts[1], parts[2], parts[3]
    sniper_id = f"sniper_{user_id}_{service}_{country}"
    
    if sniper_id in manager.active_snipers:
        manager.active_snipers[sniper_id].cancel()
        manager.active_snipers.pop(sniper_id, None)
        await callback.message.edit_text("✅ تم إيقاف الصياد بنجاح.")
    else:
        await callback.answer("⚠️ لا يوجد صياد يعمل حالياً لهذا الطلب.", show_alert=True)

# ==========================================
# 📋 إدارة التفعيلات النشطة (Active Sessions)
# ==========================================
@dp.callback_query(F.data == "menu_active")
async def list_active_sessions(callback: types.CallbackQuery):
    user_id = str(callback.from_user.id)
    active_acts = await db.get_user_activations(user_id, status="WAITING") 
    
    if not active_acts:
        builder = InlineKeyboardBuilder()
        builder.button(text="🔙 القائمة الرئيسية", callback_data="menu_main")
        await callback.message.edit_text("📭 لا يوجد لديك تفعيلات تنتظر الأكواد حالياً.", reply_markup=builder.as_markup())
        return

    text = "📋 **التفعيلات التي تنتظر الرسائل:**\n\n"
    builder = InlineKeyboardBuilder()
    
    for act in active_acts:
        act_id = act['activation_id']
        prov = act['provider']
        num = act['phone_number']
        
        text += f"📱 الرقم: `{num}` | ⚙️ {act['service']} ({prov.upper()})\n"
        
        builder.button(text=f"❌ إلغاء", callback_data=f"act_cancel_{act_id}_{prov}")
        builder.button(text=f"🚫 حظر (Ban)", callback_data=f"act_ban_{act_id}_{prov}")
        builder.button(text=f"✅ إنهاء", callback_data=f"act_finish_{act_id}_{prov}")
        builder.button(text=f"🔄 كود جديد", callback_data=f"act_resend_{act_id}_{prov}")

    builder.adjust(4) 
    builder.row(types.InlineKeyboardButton(text="🔙 القائمة الرئيسية", callback_data="menu_main"))
    
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="Markdown")

@dp.callback_query(F.data.startswith("act_"))
async def handle_activation_actions(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    action = parts[1]
    act_id = parts[2]
    provider = parts[3]
    
    await callback.answer("⏳ جاري تنفيذ الطلب...")
    
    try:
        if action == "cancel":
            success = await manager.cancel_number(act_id, provider)
            msg = "✅ تم إلغاء الرقم بنجاح واسترداد الرصيد." if success else "❌ فشل الإلغاء."
            
        elif action == "ban":
            success = await manager.ban_number(act_id, provider)
            msg = "🚫 تم حظر الرقم وإبلاغ المزود." if success else "❌ فشل حظر الرقم."
            
        elif action == "finish":
            success = await manager.finish_activation(act_id, provider)
            msg = "✅ تم إنهاء التفعيل وإغلاق الجلسة." if success else "❌ فشل إنهاء التفعيل."
            if success:
                await db.update_status(act_id, "COMPLETED")
            
        elif action == "resend":
            client = manager.grizzly if provider == "grizzly" else manager.ali
            success = await client.set_status(act_id, 3)
            msg = "🔄 تم طلب إرسال كود جديد. يرجى الانتظار..." if success else "❌ فشل طلب كود جديد."
            
        await callback.message.edit_text(msg)
        await asyncio.sleep(2)
        await list_active_sessions(callback) 
        
    except SMSAPIError as e:
        await callback.answer(f"خطأ API: {str(e)}", show_alert=True)

# ==========================================
# 🔔 نظام الإشعارات الخلفي (Background Notifier)
# ==========================================
async def notification_worker():
    logger.info("Notification Worker started.")
    while True:
        try:
            unnotified = await db.get_unnotified_completed_activations() 
            
            for act in unnotified:
                user_id = act['user_id']
                num = act['phone_number']
                code = act['code']
                sms = act['sms_text']
                
                text = (
                    "🎉 **تم التقاط الكود بنجاح!**\n\n"
                    f"📱 **الرقم:** `{num}`\n"
                    f"🔑 **الكود:** `{code}`\n\n"
                    f"💬 **النص الكامل:**\n`{sms}`\n\n"
                    "💡 _لا تنسَ الضغط على 'إنهاء' من القائمة إذا لم تكن بحاجة لكود آخر._"
                )
                
                try:
                    await bot.send_message(chat_id=user_id, text=text, parse_mode="Markdown")
                    await db.mark_as_notified(act['activation_id']) 
                except TelegramAPIError as e:
                    logger.error(f"Failed to send notification to {user_id}: {e}")
                    
        except Exception as e:
            logger.error(f"Notification worker error: {e}")
            
        await asyncio.sleep(5) 

# ==========================================
# 🌐 خادم ويب وهمي للاستضافات المجانية
# ==========================================
async def handle_ping(request):
    return web.Response(text="Bot is alive and hunting!")

async def start_dummy_server():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"✅ Dummy web server started on port {port}")

# ==========================================
# 🏁 دورة حياة التشغيل (Application Lifecycle)
# ==========================================
async def on_startup():
    logger.info("🚀 Bot is starting...")
    await manager.restore_sessions()
    asyncio.create_task(notification_worker())

async def main():
    dp.startup.register(on_startup)
    
    # 1. حذف الويب هوك لتجنب TelegramConflictError
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("🗑️ Webhook deleted and updates dropped.")
    
    # 2. تشغيل خادم الويب الوهمي
    await start_dummy_server()
    
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await manager.grizzly.close()
        await manager.ali.close()
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped cleanly.")
