"""
Central configuration. Every module reads its settings from here instead of
touching os.environ directly, so there is exactly one place to change.
"""

import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BOT_TOKEN = os.environ["BOT_TOKEN"]

# Owners: full power, can't be edited or removed from inside the bot.
OWNER_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()}

BINANCE_PAY_ID = os.environ.get("BINANCE_PAY_ID", "XXXXXXXX")
SUPPORT_CONTACT = os.environ.get("SUPPORT_CONTACT", "@your_support_username")

BINANCE_API_KEY = os.environ.get("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.environ.get("BINANCE_API_SECRET", "")
AUTO_VERIFY = bool(BINANCE_API_KEY and BINANCE_API_SECRET)

OCRSPACE_API_KEY = os.environ.get("OCRSPACE_API_KEY", "")

# Business timezone — all dates shown to you and all report periods use it.
TIMEZONE = os.environ.get("TIMEZONE", "Africa/Cairo")

CURRENCY = os.environ.get("CURRENCY", "$")

# Warn the admins when a package drops to this many SIMs or fewer.
LOW_STOCK_THRESHOLD = int(os.environ.get("LOW_STOCK_THRESHOLD", "3"))

# Default warranty in days applied to new stock when you press "تخطي".
DEFAULT_WARRANTY_DAYS = int(os.environ.get("DEFAULT_WARRANTY_DAYS", "7"))

# Most SIMs a customer may buy in a single order.
MAX_BULK_QTY = int(os.environ.get("MAX_BULK_QTY", "50"))

# Above this many SIMs in one order the codes go out as a .txt file instead of
# one QR image each.
QR_LIMIT = int(os.environ.get("QR_LIMIT", "5"))

PAGE_SIZE = 8

# ---------------- customer menu (bottom keyboard) ----------------
MENU_BROWSE = "🌍 الشرايح المتاحة"
MENU_TOPUP = "💳 شحن الرصيد"
MENU_MY_ESIMS = "🧾 شرايحي"
MENU_BALANCE = "💰 رصيدي"
MENU_SUPPORT = "🧑‍💻 الدعم الفني"
MENU_HELP = "🔥 كيفية الاستخدام"
MENU_ADMIN = "⚙️ لوحة التحكم"

# ---------------- admin panel (bottom keyboard) ----------------
ADMIN_CATALOG = "🗂️ الدول والباقات"
ADMIN_TIERS = "🏷️ أسعار الجملة"
ADMIN_STOCK = "📦 المخزون"
ADMIN_ADD_STOCK = "➕ إضافة ستوك"
ADMIN_SEARCH = "🔍 بحث شامل"
ADMIN_REPORTS = "📊 التقارير المالية"
ADMIN_INVENTORY = "📦 قيمة المخزون"
ADMIN_CUSTOMERS = "👥 العملاء"
ADMIN_CREDIT = "💰 شحن / خصم رصيد"
ADMIN_BROADCAST = "📢 رسالة جماعية"
ADMIN_WARRANTY = "🛡️ طلبات الضمان"
ADMIN_SETTINGS = "⚙️ الإعدادات"
ADMIN_STAFF = "🔑 الأدمن والصلاحيات"
ADMIN_BACK = "🔙 رجوع لقائمة المتجر"
ADMIN_REFUNDS = "💳 استرجاع الرصيد"
