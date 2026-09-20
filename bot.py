"""
The main bot entry point. Sets up the Telegram dispatcher with all the handlers
(commands, messages, button clicks) that route to the customer and admin logic.
"""

import logging
import re
import sys

from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

import config
import db
import handlers_admin
import handlers_search
import handlers_user
import permissions as perms
import ui

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s — %(name)s — %(levelname)s — %(message)s",
)
log = logging.getLogger(__name__)


async def start(update, context):
    """The /start command."""
    user = update.effective_user
    db.ensure_user(user.id, user.username, user.first_name)
    text = (
        f"👋 أهلاً {user.first_name or 'عميلنا'}!\n\n"
        "🛒 في متجر خدمات eSIM دولية بأسعار تنافسية.\n"
        "📲 اختر الباقة اللي تناسبك واحصل على شريحة فورًا.\n\n"
        "💡 <i>الشرائح مرسلة عبر بيانات، بدون بطاقة SIM فيزيائية.</i>\n\n"
        f"{db.get_delivery_note()}"
    )
    await update.message.reply_text(text, reply_markup=ui.main_menu_keyboard(user.id),
                                    parse_mode="HTML")


async def admin_panel(update, context):
    """The /admin command."""
    if not perms.is_staff(update.effective_user.id):
        return
    await handlers_admin.panel(update, context)


async def help_cmd(update, context):
    """The /help command."""
    text = (
        "❓ <b>الأسئلة الشائعة</b>\n\n"
        "❓ كيف أشتري شريحة eSIM؟\n"
        "اضغط على 🛒 المتجر، اختر الباقة، وادفع برصيدك.\n\n"
        "❓ كيف أشحن رصيدي؟\n"
        "اضغط على 💳 شحن وفي خيارات الدفع المتاحة.\n\n"
        "❓ الشريحة ماوصلتش؟\n"
        "ممكن يكون فيه تأخير قليل، لو استنيت ساعة ولم تصل كلم الدعم.\n\n"
        f"📞 <b>الدعم</b>\n{config.SUPPORT_CONTACT}"
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def text_message(update, context):
    """Catch-all for text messages outside of conversations.
    Routes to admin panel or shows error."""
    user_id = update.effective_user.id
    text = update.message.text

    # Refunds button from admin keyboard
    if text == "💳 طلبات الاسترجاع":
        if perms.is_staff(user_id) and perms.can(user_id, perms.P_WALLET):
            await handlers_admin.refunds_panel(update, context)
        else:
            await update.message.reply_text("⛔ الصلاحية دي مش معاك.")
        return

    # Back to shop button
    if text == "🔙 رجوع لقائمة المتجر":
        await handlers_admin.back_to_shop(update, context)
        return

    # Admin keyboard buttons for various panels
    if perms.is_staff(user_id):
        patterns = [
            (config.ADMIN_CATALOG, handlers_admin.catalog_panel),
            (config.ADMIN_TIERS, handlers_admin.pricing_panel),
            (config.ADMIN_STOCK, handlers_admin.stock_panel),
            (config.ADMIN_SEARCH, handlers_search.search_start),
            (config.ADMIN_CUSTOMERS, handlers_admin.customers),
            (config.ADMIN_REPORTS, handlers_admin.reports_panel),
            (config.ADMIN_INVENTORY, handlers_admin.inventory_panel),
            (config.ADMIN_CREDIT, handlers_admin.credit_start),
            (config.ADMIN_WARRANTY, handlers_admin.claims_panel),
            (config.ADMIN_BROADCAST, handlers_admin.broadcast_start),
            (config.ADMIN_SETTINGS, handlers_admin.settings_panel),
        ]
        for pattern, handler in patterns:
            if pattern and text == pattern:
                await handler(update, context)
                return

    # Not recognized
    if not perms.is_staff(user_id):
        await update.message.reply_text("❌ ماتعرفتش الأمر ده. استخدم القائمة 👆",
                                       reply_markup=ui.main_menu_keyboard(user_id))


async def error_handler(update, context):
    """Logs errors caused by updates."""
    log.error("Exception while handling an update:", exc_info=context.error)


def main():
    """Run the bot."""
    app = Application.builder().token(config.BOT_TOKEN).build()

    # Command handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("admin", admin_panel))

    # Search
    app.add_handler(handlers_search.search_conv)
    app.add_handler(CallbackQueryHandler(handlers_search.callbacks, pattern=r"^srch:"))

    # User side: conversations
    app.add_handler(handlers_user.qty_conv)
    app.add_handler(handlers_user.claim_conv)
    app.add_handler(handlers_user.refund_conv)
    
    # User side: top-up (text/photo handlers, not ConversationHandler)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers_user.topup_text))
    app.add_handler(MessageHandler(filters.PHOTO, handlers_user.topup_photo))
    
    # User side: callbacks
    app.add_handler(CallbackQueryHandler(handlers_user.callbacks, pattern=r"^u:"))

    # Admin: panels and actions
    app.add_handler(handlers_admin.credit_conv)
    app.add_handler(handlers_admin.broadcast_conv)
    app.add_handler(handlers_admin.maint_conv)
    app.add_handler(handlers_admin.note_conv)
    app.add_handler(handlers_admin.staff_conv)
    
    # Admin: refunds (✅ ALREADY WORKING)
    app.add_handler(handlers_admin.refund_conv)
    app.add_handler(handlers_admin.refund_reject_conv)
    
    # Admin: callbacks
    app.add_handler(CallbackQueryHandler(handlers_admin.callbacks, pattern=r"^adm:"))

    # Generic text message handler (catch menu buttons, fallback)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message))

    # Error handler
    app.add_error_handler(error_handler)

    # Start polling
    log.info("Bot started, polling...")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Bot stopped by user.")
        sys.exit(0)
