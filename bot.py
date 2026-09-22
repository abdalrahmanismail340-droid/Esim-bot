"""
Entry point. Wires every module together and starts polling.

The admin side is one inline panel (/admin → adm:home). The bottom keyboard
only carries the customer menu plus a single key that opens that panel.

Handler order matters: conversations are registered first so their entry points
win over the generic callback routers, and the free-text catch-all is last.
"""

import logging
import re

from telegram import BotCommand, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import config
import db
import handlers_admin as adm
import handlers_catalog as cat
import handlers_reports as rep
import handlers_search as srch
import handlers_stock as stk
import handlers_user as usr
import handlers_refunds as refunds
import ui

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger(__name__)


def menu(label):
    return filters.Regex(f"^{re.escape(label)}$")


async def noop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()


async def on_error(update, context):
    log.error("handler error", exc_info=context.error)


async def post_init(app: Application):
    await app.bot.set_my_commands([
        BotCommand("start", "ابدأ من الأول"),
        BotCommand("help", "ازاي تشتري"),
        BotCommand("admin", "لوحة التحكم (للأدمن)"),
        BotCommand("search", "بحث (للأدمن)"),
        BotCommand("cancel", "إلغاء العملية الحالية"),
    ])


def main():
    db.init_db()
    app = Application.builder().token(config.BOT_TOKEN).post_init(post_init).build()

    # ---- conversations first (their entry points must beat the routers) ----
    app.add_handler(cat.addcountry_conv)
    app.add_handler(cat.editcountry_conv)
    app.add_handler(cat.addplan_conv)
    app.add_handler(cat.editplan_conv)
    app.add_handler(cat.tier_conv)
    app.add_handler(stk.addstock_conv)
    app.add_handler(srch.search_conv)
    app.add_handler(adm.credit_conv)
    app.add_handler(adm.broadcast_conv)
    app.add_handler(adm.maint_conv)
    app.add_handler(adm.note_conv)
    app.add_handler(adm.staff_conv)
    app.add_handler(usr.claim_conv)
    app.add_handler(usr.qty_conv)
    app.add_handler(refunds.refund_conv)

    # ---- commands ----
    app.add_handler(CommandHandler("start", usr.start))
    app.add_handler(CommandHandler("help", usr.help_cmd))
    app.add_handler(CommandHandler("admin", adm.panel))

    # ---- bottom keyboard ----
    app.add_handler(MessageHandler(menu(config.MENU_BROWSE), usr.browse))
    app.add_handler(MessageHandler(menu(config.MENU_TOPUP), usr.topup))
    app.add_handler(MessageHandler(menu(config.MENU_MY_ESIMS), usr.my_esims))
    app.add_handler(MessageHandler(menu(config.MENU_BALANCE), usr.wallet))
    app.add_handler(MessageHandler(menu(config.MENU_SUPPORT), usr.support))
    app.add_handler(MessageHandler(menu(config.MENU_HELP), usr.help_cmd))
    app.add_handler(MessageHandler(menu(config.MENU_ADMIN), adm.open_panel_button))

    # ---- callback routers ----
    app.add_handler(CallbackQueryHandler(noop, pattern=r"^noop$"))
    app.add_handler(CallbackQueryHandler(usr.callbacks, pattern=r"^u:"))
    app.add_handler(CallbackQueryHandler(cat.callbacks, pattern=r"^cat:"))
    app.add_handler(CallbackQueryHandler(stk.callbacks, pattern=r"^stk:"))
    app.add_handler(CallbackQueryHandler(srch.callbacks, pattern=r"^srch:"))
    app.add_handler(CallbackQueryHandler(rep.callbacks, pattern=r"^rep:"))
    app.add_handler(CallbackQueryHandler(adm.callbacks, pattern=r"^adm:"))

    # ---- free text / photos (top-up flow) ----
    app.add_handler(MessageHandler(filters.PHOTO, usr.topup_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, usr.topup_text))

    app.add_error_handler(on_error)

    log.info("owners: %s", sorted(config.OWNER_IDS) or "⚠️ مفيش! حط ADMIN_IDS في .env")
    log.info("bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
