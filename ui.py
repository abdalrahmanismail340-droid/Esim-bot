"""
Everything shared by the handler modules: keyboards, formatting, time handling,
access guards and admin notifications.
"""

import functools
import logging
from datetime import datetime, timedelta, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.constants import ParseMode

import config
import db
import permissions as perms

log = logging.getLogger(__name__)

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo(config.TIMEZONE)
except Exception:  # tzdata missing on a slim image — fall back to a fixed offset
    TZ = timezone(timedelta(hours=3))
    log.warning("timezone %s unavailable, using UTC+3", config.TIMEZONE)

DB_FMT = "%Y-%m-%d %H:%M:%S"


# ---------------------------------------------------------------- time

def now_local() -> datetime:
    return datetime.now(TZ)


def parse_db_time(value):
    """Reads both the old ISO format ('...T...') and the new one ('... ...')."""
    if not value:
        return None
    text = str(value).replace("T", " ").split(".")[0].replace("Z", "").strip()
    try:
        return datetime.strptime(text, DB_FMT).replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None


def fmt_dt(value, with_time=True) -> str:
    dt = parse_db_time(value)
    if not dt:
        return "—"
    local = dt.astimezone(TZ)
    return local.strftime("%Y-%m-%d %H:%M") if with_time else local.strftime("%Y-%m-%d")


def days_left(value):
    """Whole days remaining until a stored UTC timestamp. Negative when expired."""
    dt = parse_db_time(value)
    if not dt:
        return None
    seconds = (dt - datetime.now(timezone.utc)).total_seconds()
    if seconds < 0:
        return int(seconds // 86400)
    return int(-(-seconds // 86400))  # round up: a fresh 7-day warranty reads 7


def warranty_text(warranty_until) -> str:
    if not warranty_until:
        return "🚫 بدون ضمان"
    left = days_left(warranty_until)
    if left is None:
        return "🚫 بدون ضمان"
    if left < 0:
        return f"⌛ الضمان انتهى ({fmt_dt(warranty_until, False)})"
    return f"🛡️ ساري — باقي {left} يوم (لحد {fmt_dt(warranty_until, False)})"


PERIODS = {
    "today": "النهاردة",
    "yesterday": "امبارح",
    "7d": "آخر ٧ أيام",
    "30d": "آخر ٣٠ يوم",
    "month": "الشهر ده",
    "all": "من البداية",
}


def period_bounds(key):
    """Returns (start_utc_str, end_utc_str, label) for a report period, using the
    business timezone for the day boundaries."""
    today = now_local().replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow = today + timedelta(days=1)
    if key == "today":
        start, end = today, tomorrow
    elif key == "yesterday":
        start, end = today - timedelta(days=1), today
    elif key == "7d":
        start, end = today - timedelta(days=6), tomorrow
    elif key == "30d":
        start, end = today - timedelta(days=29), tomorrow
    elif key == "month":
        start, end = today.replace(day=1), tomorrow
    else:
        start, end = datetime(2000, 1, 1, tzinfo=TZ), tomorrow
    to_utc = lambda d: d.astimezone(timezone.utc).strftime(DB_FMT)
    return to_utc(start), to_utc(end), PERIODS.get(key, key)


def warranty_until_from(days):
    if not days or int(days) <= 0:
        return None
    return (datetime.now(timezone.utc) + timedelta(days=int(days))).strftime(DB_FMT)


# ---------------------------------------------------------------- formatting

def money(amount) -> str:
    return f"{float(amount or 0):.2f}{config.CURRENCY}"


def mask_code(code, keep=6) -> str:
    """Shortens an LPA code for list views so a screenshot can't leak a full one."""
    if not code:
        return "—"
    code = str(code)
    return code if len(code) <= keep * 2 else f"{code[:keep]}…{code[-keep:]}"


def user_tag(user) -> str:
    if getattr(user, "username", None):
        return f"@{user.username} (<code>{user.id}</code>)"
    return f"{user.first_name} (<code>{user.id}</code>)"


def row_tag(row) -> str:
    """Same idea, but for a DB row instead of a telegram user object."""
    name = row.get("username") or row.get("first_name") or "—"
    uid = row.get("telegram_id") or row.get("user_id")
    return f"@{name} (<code>{uid}</code>)" if row.get("username") else f"{name} (<code>{uid}</code>)"


STATUS_AR = {
    "available": "🟢 متاحة في المخزون",
    "sold": "🔴 مباعة",
    "void": "⚫ ملغية",
    "pending": "⏳ قيد التنفيذ",
    "approved": "✅ تم التسليم",
    "rejected": "❌ مرفوض",
    "refunded": "↩️ مسترجع",
    "open": "🟠 مفتوح",
    "resolved": "✅ اتحل",
    "declined": "❌ مرفوض",
}

LEDGER_AR = {
    "topup": "💳 شحن",
    "manual_credit": "➕ شحن يدوي",
    "manual_debit": "➖ خصم يدوي",
    "purchase": "🛒 شراء",
    "refund": "↩️ استرجاع",
}


def bar(value, maximum, width=10) -> str:
    """Tiny text bar used inside the reports."""
    if not maximum:
        return "░" * width
    filled = int(round((value / maximum) * width))
    return "█" * max(0, min(width, filled)) + "░" * (width - max(0, min(width, filled)))


# ---------------------------------------------------------------- keyboards

def main_menu_keyboard(user_id=None):
    rows = [
        [config.MENU_BROWSE, config.MENU_TOPUP],
        [config.MENU_MY_ESIMS, config.MENU_BALANCE],
        [config.MENU_SUPPORT, config.MENU_HELP],
    ]
    if user_id is not None and perms.is_staff(user_id):
        rows.append([config.MENU_ADMIN])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def hub_btn():
    """Every admin screen carries this so the panel is always one tap away."""
    return InlineKeyboardButton("⚙️ لوحة التحكم", callback_data="adm:home")


def kb(rows):
    return InlineKeyboardMarkup(rows)


def btn(text, data):
    return InlineKeyboardButton(text, callback_data=data)


def back_btn(data, text="⬅️ رجوع"):
    return [InlineKeyboardButton(text, callback_data=data)]


def paginate(items, page, page_size=None):
    """Returns (slice, total_pages). Page numbers are 0-based."""
    page_size = page_size or config.PAGE_SIZE
    total_pages = max(1, (len(items) + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))
    return items[page * page_size:(page + 1) * page_size], total_pages


def pager_row(prefix, page, total_pages):
    if total_pages <= 1:
        return []
    row = []
    if page > 0:
        row.append(btn("◀️", f"{prefix}{page - 1}"))
    row.append(btn(f"{page + 1}/{total_pages}", "noop"))
    if page < total_pages - 1:
        row.append(btn("▶️", f"{prefix}{page + 1}"))
    return row


# ---------------------------------------------------------------- guards

async def guard(update: Update) -> bool:
    """True when this user shouldn't be served right now — banned, or the bot is
    in maintenance. Staff bypass both, so you can keep working while closed."""
    user = update.effective_user
    if not user or perms.is_staff(user.id):
        return False

    msg = update.message or (update.callback_query.message if update.callback_query else None)

    if db.is_banned(user.id):
        if update.callback_query:
            await update.callback_query.answer("محظور من استخدام البوت.", show_alert=True)
        elif msg:
            await msg.reply_text("🚫 أنت محظور من استخدام البوت.")
        return True

    if db.is_maintenance():
        notice = db.get_maintenance_message()
        if update.callback_query:
            await update.callback_query.answer(notice, show_alert=True)
        elif msg:
            await msg.reply_text(notice)
        return True

    return False


def require(perm):
    """Decorator for admin handlers. Silently ignores non-staff, and tells a
    staff member with the wrong role that this button isn't theirs."""
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(update: Update, context, *args, **kwargs):
            user = update.effective_user
            if not user or not perms.is_staff(user.id):
                return
            if not perms.can(user.id, perm):
                if update.callback_query:
                    await update.callback_query.answer("الصلاحية دي مش معاك.", show_alert=True)
                elif update.message:
                    await update.message.reply_text("⛔ الصلاحية دي مش معاك.")
                return
            return await func(update, context, *args, **kwargs)
        return wrapper
    return decorator


async def reply(update: Update, text, **kwargs):
    """Answers whether we arrived here from a message or a callback query."""
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, **kwargs)
            return
        except Exception:
            await update.callback_query.message.reply_text(text, **kwargs)
            return
    await update.message.reply_text(text, **kwargs)


# ---------------------------------------------------------------- notifications

async def notify_admins(context, text, perm=None):
    """Sends an event to every admin — optionally only those holding `perm`.
    Never raises: a failed notification must not break a customer flow."""
    targets = set(config.OWNER_IDS)
    for row in db.list_staff():
        uid = row["user_id"]
        if perm is None or perms.can(uid, perm):
            targets.add(uid)
    for admin_id in targets:
        try:
            await context.bot.send_message(admin_id, text, parse_mode=ParseMode.HTML)
        except Exception as e:
            log.warning("couldn't notify admin %s: %s", admin_id, e)
