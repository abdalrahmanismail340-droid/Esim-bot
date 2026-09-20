"""
One search box for everything.

Type part of an LPA code, an order number, a user id, a @username, a supplier
name or a country — the bot figures out what you meant and shows whatever
matches: SIMs, orders and customers.
"""

import logging
import re

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import config
import db
import handlers_stock
import permissions as perms
import ui

log = logging.getLogger(__name__)

P = perms.P_SEARCH
ASK_QUERY = 800

PROMPT = (
    "🔍 <b>بحث شامل</b>\n\n"
    "اكتب أي حاجة من دول:\n"
    "• جزء من كود الشريحة (LPA)\n"
    "• رقم الطلب (مثال: 45 أو #45)\n"
    "• ID العميل أو اليوزرنيم\n"
    "• اسم المورد أو الدولة أو رقم الدفعة\n\n"
    "/cancel للإلغاء"
)


@ui.require(P)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await ui.reply(update, PROMPT, parse_mode=ParseMode.HTML)
    return ASK_QUERY


async def run(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.message.text.strip().lstrip("#").strip()
    if not q:
        await update.message.reply_text("اكتب حاجة أدور عليها.")
        return ASK_QUERY

    sims = db.search_stock(q, limit=10)
    orders = db.search_orders(q, limit=10)
    users = db.find_users(q, limit=10)

    if not sims and not orders and not users:
        await update.message.reply_text(
            f"❌ ملقيتش أي نتيجة لـ «{q}».\n\nجرب جزء أقصر من الكود أو ID العميل."
        )
        return ConversationHandler.END

    lines = [f"🔍 نتائج البحث عن «<b>{q}</b>»\n"]
    rows = []

    if sims:
        lines.append(f"📦 <b>شرايح ({len(sims)})</b>")
        for s in sims[:6]:
            lines.append(
                f"• #{s['id']} {ui.mask_code(s['lpa_code'])} — {s.get('country') or '—'} "
                f"{s.get('data_amount') or ''} · {ui.STATUS_AR.get(s['status'], s['status'])}"
            )
            rows.append([ui.btn(f"🔎 شريحة #{s['id']} — {ui.mask_code(s['lpa_code'], 4)}",
                                f"srch:sim:{s['id']}")])
        lines.append("")

    if orders:
        lines.append(f"🧾 <b>طلبات ({len(orders)})</b>")
        for o in orders[:6]:
            lines.append(
                f"• #{o['id']} — {o.get('country') or ''} {o.get('data_amount') or ''} · "
                f"{ui.money(o['price'])} · {ui.STATUS_AR.get(o['status'], o['status'])} · "
                f"{ui.fmt_dt(o['created_at'], False)}"
            )
            rows.append([ui.btn(f"🧾 طلب #{o['id']}", f"srch:o:{o['id']}")])
        lines.append("")

    if users:
        lines.append(f"👤 <b>عملاء ({len(users)})</b>")
        for u in users[:6]:
            lines.append(
                f"• {u.get('username') or u.get('first_name') or '—'} "
                f"(<code>{u['telegram_id']}</code>) · رصيد {ui.money(u['balance'])}"
            )
            rows.append([ui.btn(f"👤 {u.get('username') or u['telegram_id']}",
                                f"srch:u:{u['telegram_id']}")])

    rows.append([ui.btn("🔍 بحث تاني", "srch:start"), ui.hub_btn()])
    await update.message.reply_text("\n".join(lines), reply_markup=ui.kb(rows),
                                    parse_mode=ParseMode.HTML)
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("اتلغى.")
    return ConversationHandler.END


# ---------------------------------------------------------------- detail views

def order_card(order_id: int) -> str:
    o = db.order_detail(order_id)
    if not o:
        return "❌ الطلب ده مش موجود."
    profit = (o["price"] or 0) - (o.get("cost_price") or 0)
    lines = [
        f"🧾 <b>الطلب #{o['id']}</b>",
        f"🏷️ الحالة: {ui.STATUS_AR.get(o['status'], o['status'])}",
        f"👤 العميل: {('@' + o['username']) if o.get('username') else '—'} "
        f"(<code>{o['user_id']}</code>)",
        f"📦 {o.get('product_label') or db.product_label(o)}",
        f"💵 سعر البيع: {ui.money(o['price'])}",
        f"🏭 التكلفة: {ui.money(o.get('cost_price'))} · 📈 الربح: {ui.money(profit)}",
        f"🕒 وقت الطلب: {ui.fmt_dt(o['created_at'])}",
    ]
    if o.get("delivered_at"):
        lines.append(f"📤 وقت التسليم: {ui.fmt_dt(o['delivered_at'])}")
    if o.get("lpa_code"):
        lines.append(f"🔑 الشريحة #{o.get('sim_id')}: <code>{o['lpa_code']}</code>")
        lines.append(f"🏭 المورد: {o.get('supplier') or '—'}")
    lines.append(ui.warranty_text(o.get("warranty_until")))
    return "\n".join(lines)


def user_card(user_id: int) -> str:
    u = db.get_user(user_id)
    if not u:
        return "❌ العميل ده مش موجود."
    orders = db.get_user_orders(user_id, limit=100)
    approved = [o for o in orders if o["status"] == "approved"]
    spent = sum(o["price"] for o in approved)

    lines = [
        f"👤 <b>{u.get('username') and '@' + u['username'] or u.get('first_name') or 'عميل'}</b>",
        f"🆔 <code>{u['telegram_id']}</code>",
        f"💰 الرصيد الحالي: <b>{ui.money(u['balance'])}</b>",
        f"🛒 المشتريات: {len(approved)} شريحة بإجمالي {ui.money(spent)}",
        f"📅 أول دخول: {ui.fmt_dt(u.get('joined_at'), False)}",
        f"👁️ آخر نشاط: {ui.fmt_dt(u.get('last_seen'))}",
        f"🚫 محظور: {'أيوه' if u.get('is_banned') else 'لأ'}"
        + (" · 🙈 حاظر البوت" if u.get("blocked_bot") else ""),
    ]
    if approved:
        lines.append("\n🧾 <b>آخر مشترياته</b>")
        for o in approved[:5]:
            lines.append(
                f"• #{o['id']} {o.get('product_label') or ''} · {ui.money(o['price'])} · "
                f"{ui.fmt_dt(o['created_at'], False)} · {ui.warranty_text(o.get('warranty_until'))}"
            )
    ledger = db.get_user_ledger(user_id, limit=5)
    if ledger:
        lines.append("\n📜 <b>آخر حركات الرصيد</b>")
        for h in ledger:
            sign = "+" if h["amount"] > 0 else ""
            lines.append(
                f"• {ui.LEDGER_AR.get(h['kind'], h['kind'])} {sign}{ui.money(h['amount'])} "
                f"→ {ui.money(h['balance_after'])} · {ui.fmt_dt(h['created_at'], False)}"
            )
    return "\n".join(lines)


@ui.require(P)
async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    action = parts[1]

    if action == "sim":
        sid = int(parts[2])
        await query.edit_message_text(handlers_stock.sim_card(sid),
                                      reply_markup=handlers_stock.sim_buttons(sid, back="srch:start"),
                                      parse_mode=ParseMode.HTML)
    elif action == "o":
        oid = int(parts[2])
        o = db.order_detail(oid)
        rows = []
        if o and o.get("sim_id"):
            rows.append([ui.btn(f"🔎 الشريحة #{o['sim_id']}", f"srch:sim:{o['sim_id']}")])
        if o:
            rows.append([ui.btn("👤 العميل", f"srch:u:{o['user_id']}")])
        rows.append([ui.btn("🔍 بحث تاني", "srch:start"), ui.hub_btn()])
        await query.edit_message_text(order_card(oid), reply_markup=ui.kb(rows) if rows else None,
                                      parse_mode=ParseMode.HTML)
    elif action == "u":
        uid = int(parts[2])
        rows = []
        if perms.can(query.from_user.id, perms.P_WALLET):
            rows.append([ui.btn("💰 شحن رصيد للعميل ده", f"adm:credituser:{uid}")])
        if perms.can(query.from_user.id, perms.P_USERS):
            banned = db.is_banned(uid)
            rows.append([ui.btn("✅ فك الحظر" if banned else "🚫 حظر", f"adm:ban:{uid}")])
        rows.append([ui.btn("🔍 بحث تاني", "srch:start"), ui.hub_btn()])
        await query.edit_message_text(user_card(uid), reply_markup=ui.kb(rows) if rows else None,
                                      parse_mode=ParseMode.HTML)


search_conv = ConversationHandler(
    entry_points=[
        CommandHandler("search", start),
        MessageHandler(filters.Regex(f"^{re.escape(config.ADMIN_SEARCH)}$"), start),
        CallbackQueryHandler(start, pattern=r"^srch:start$"),
    ],
    states={ASK_QUERY: [MessageHandler(filters.TEXT & ~filters.COMMAND, run)]},
    fallbacks=[CommandHandler("cancel", cancel),
               MessageHandler(ui.MENU_ESCAPE, cancel)],
)
