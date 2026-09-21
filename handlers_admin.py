"""
The admin side.

Everything lives inside one inline panel (`/admin` → adm:home): catalogue,
bulk pricing, stock, search, reports, customers, wallet, warranty, broadcast,
staff and settings. Every screen carries a ⚙️ button back to that panel, and
each button only shows up for admins who hold the matching permission.
"""

import asyncio
import csv
import io
import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import Forbidden
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
import handlers_search
import handlers_user
import permissions as perms
import ui

log = logging.getLogger(__name__)


# ---------------------------------------------------------------- the hub

def hub_keyboard(uid):
    """The panel. Built from this admin's permissions, two buttons per row."""
    rows = []

    def pair(*buttons):
        buttons = [b for b in buttons if b]
        for i in range(0, len(buttons), 2):
            rows.append(buttons[i:i + 2])

    pair(
        ui.btn(config.ADMIN_CATALOG, "cat:home") if perms.can(uid, perms.P_CATALOG) else None,
        ui.btn(config.ADMIN_TIERS, "cat:tierpick") if perms.can(uid, perms.P_CATALOG) else None,
    )
    pair(
        ui.btn(config.ADMIN_STOCK, "stk:home") if perms.can(uid, perms.P_STOCK) else None,
        ui.btn(config.ADMIN_ADD_STOCK, "stk:addnew") if perms.can(uid, perms.P_STOCK) else None,
    )
    pair(
        ui.btn(config.ADMIN_SEARCH, "srch:start") if perms.can(uid, perms.P_SEARCH) else None,
        ui.btn(config.ADMIN_CUSTOMERS, "adm:customers") if perms.can(uid, perms.P_USERS) else None,
    )
    pair(
        ui.btn(config.ADMIN_REPORTS, "rep:home") if perms.can(uid, perms.P_REPORTS) else None,
        ui.btn(config.ADMIN_INVENTORY, "rep:inv") if perms.can(uid, perms.P_REPORTS) else None,
    )
    open_claims = db.count_open_claims()
    claims_label = config.ADMIN_WARRANTY + (f" ({open_claims})" if open_claims else "")
    pair(
        ui.btn(config.ADMIN_CREDIT, "adm:creditstart") if perms.can(uid, perms.P_WALLET) else None,
        ui.btn(claims_label, "adm:claims:open") if perms.can(uid, perms.P_WARRANTY) else None,
    )
    pair(
        ui.btn(config.ADMIN_BROADCAST, "adm:bcaststart") if perms.can(uid, perms.P_BROADCAST) else None,
        ui.btn(config.ADMIN_SETTINGS, "adm:settings") if perms.can(uid, perms.P_SETTINGS) else None,
    )
    if perms.is_owner(uid):
        rows.append([ui.btn(config.ADMIN_STAFF, "adm:staff")])
    rows.append([ui.btn("🔄 تحديث", "adm:home")])
    return ui.kb(rows)


def hub_text(uid):
    counts = db.stock_counts()
    balances = db.total_customer_balance()
    value = db.inventory_value()
    start, end, _ = ui.period_bounds("today")
    today = db.sales_summary(start, end)
    profit_today = (today["revenue"] or 0) - (today["cost"] or 0)

    text = (
        "⚙️ <b>لوحة التحكم</b>\n"
        f"{perms.describe(uid)}\n\n"
        "<b>📅 النهاردة</b>\n"
        f"🛒 {today['orders']} عملية / {today['sims']} شريحة · "
        f"💵 {ui.money(today['revenue'])} · "
        f"📈 ربح {ui.money(profit_today)}\n\n"
        "<b>📦 المخزون</b>\n"
        f"🟢 {counts['available']} متاحة · 🔴 {counts['sold']} مباعة · "
        f"💵 بتكلفة {ui.money(value['cost_value'])}\n\n"
        "<b>👥 العملاء</b>\n"
        f"{db.count_users()} عميل · 💰 أرصدتهم {ui.money(balances['total'])}"
    )
    low = db.low_stock_products(config.LOW_STOCK_THRESHOLD)
    if low:
        text += f"\n\n⚠️ {len(low)} باقة قربت تخلص"
    if db.is_maintenance():
        text += "\n\n🛠️ <b>البوت في وضع الصيانة</b>"
    return text


async def panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not perms.is_staff(uid):
        return
    await ui.reply(update, hub_text(uid), reply_markup=hub_keyboard(uid),
                   parse_mode=ParseMode.HTML)


async def open_panel_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """The '⚙️ لوحة التحكم' key on the bottom keyboard."""
    await panel(update, context)


# ---------------------------------------------------------------- customers

@ui.require(perms.P_USERS)
async def customers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    total = db.count_users()
    balances = db.total_customer_balance()
    banned = db.list_banned_users()
    blockers = db.count_blocked_bot()
    text = (
        "👥 <b>العملاء</b>\n\n"
        f"• الإجمالي: {total}\n"
        f"• عندهم رصيد: {balances['holders']} بإجمالي {ui.money(balances['total'])}\n"
        f"• محظورين: {len(banned)}\n"
        f"• حاظرين البوت: {blockers}"
    )
    rows = [
        [ui.btn("🔍 دور على عميل", "srch:start")],
        [ui.btn("💰 أعلى أرصدة", "adm:topusers"), ui.btn("🆕 أحدث العملاء", "adm:newusers")],
        [ui.btn("🚫 المحظورين", "adm:banned"), ui.btn("🙈 حاظرين البوت", "adm:blockers")],
        [ui.hub_btn()],
    ]
    await ui.reply(update, text, reply_markup=ui.kb(rows), parse_mode=ParseMode.HTML)


@ui.require(perms.P_USERS)
async def user_list(update: Update, context: ContextTypes.DEFAULT_TYPE, order):
    users = db.list_users(limit=15, order=order)
    title = "💰 أعلى أرصدة" if order == "balance" else "🆕 أحدث العملاء"
    lines = [f"<b>{title}</b>\n"]
    rows = []
    for u in users:
        name = u.get("username") or u.get("first_name") or "—"
        lines.append(f"• {name} (<code>{u['telegram_id']}</code>) — {ui.money(u['balance'])}")
        rows.append([ui.btn(f"👤 {name}", f"srch:u:{u['telegram_id']}")])
    rows.append([ui.btn("⬅️ رجوع", "adm:customers"), ui.hub_btn()])
    await ui.reply(update, "\n".join(lines), reply_markup=ui.kb(rows), parse_mode=ParseMode.HTML)


@ui.require(perms.P_USERS)
async def banned_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows_db = db.list_banned_users()
    if not rows_db:
        await ui.reply(update, "مفيش محظورين.",
                       reply_markup=ui.kb([[ui.btn("⬅️ رجوع", "adm:customers"), ui.hub_btn()]]))
        return
    rows = [[ui.btn(f"👤 {u.get('username') or u['telegram_id']}", f"srch:u:{u['telegram_id']}")]
            for u in rows_db]
    rows.append([ui.btn("⬅️ رجوع", "adm:customers"), ui.hub_btn()])
    await ui.reply(update, f"🚫 <b>المحظورين ({len(rows_db)})</b>", reply_markup=ui.kb(rows),
                   parse_mode=ParseMode.HTML)


@ui.require(perms.P_USERS)
async def blockers_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows_db = db.list_blocked_bot_users()
    back = [[ui.btn("⬅️ رجوع", "adm:customers"), ui.hub_btn()]]
    if not rows_db:
        await ui.reply(update, "مفيش حد حاظر البوت حاليًا 👍", reply_markup=ui.kb(back))
        return
    lines = [f"🙈 <b>حاظرين البوت ({len(rows_db)})</b>",
             "<i>القائمة بتتحدث مع كل رسالة جماعية.</i>\n"]
    for u in rows_db[:30]:
        lines.append(f"• {u.get('username') or '—'} (<code>{u['telegram_id']}</code>) — "
                     f"{ui.money(u['balance'])}")
    await ui.reply(update, "\n".join(lines), reply_markup=ui.kb(back), parse_mode=ParseMode.HTML)


@ui.require(perms.P_USERS)
async def toggle_ban(update: Update, context: ContextTypes.DEFAULT_TYPE, uid: int):
    query = update.callback_query
    if perms.is_staff(uid):
        await query.answer("مينفعش تحظر أدمن.", show_alert=True)
        return
    banned = db.is_banned(uid)
    db.set_banned(uid, not banned)
    db.log_action(query.from_user.id, "unban" if banned else "ban", str(uid))
    try:
        await context.bot.send_message(
            uid, "✅ اترفع الحظر عنك، تقدر تستخدم البوت تاني."
            if banned else "🚫 اتحظرت من استخدام البوت."
        )
    except Exception:
        pass
    await query.answer("اترفع الحظر ✅" if banned else "اتحظر 🚫", show_alert=True)
    await query.edit_message_text(
        handlers_search.user_card(uid),
        reply_markup=ui.kb([[ui.btn("✅ فك الحظر" if not banned else "🚫 حظر", f"adm:ban:{uid}")],
                            [ui.hub_btn()]]),
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------- manual credit

CREDIT_USER, CREDIT_AMOUNT, CREDIT_CONFIRM = range(400, 403)


@ui.require(perms.P_WALLET)
async def credit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
    await ui.reply(
        update,
        "💰 شحن / خصم رصيد\n\nابعت ID العميل (رقم) أو اليوزرنيم.\n\n/cancel للإلغاء",
    )
    return CREDIT_USER


@ui.require(perms.P_WALLET)
async def credit_for_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry from a customer card: the user is already known."""
    query = update.callback_query
    await query.answer()
    uid = int(query.data.split(":")[2])
    context.user_data["credit_uid"] = uid
    u = db.get_user(uid)
    await query.edit_message_text(
        f"💰 شحن رصيد لـ <code>{uid}</code>\n"
        f"الرصيد الحالي: {ui.money(u['balance'] if u else 0)}\n\n"
        "اكتب المبلغ. رقم موجب = شحن، وبالسالب = خصم (مثال: -5).\n\n/cancel للإلغاء",
        parse_mode=ParseMode.HTML,
    )
    return CREDIT_AMOUNT


async def credit_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text.lstrip("-").isdigit():
        uid = int(text)
    else:
        found = db.find_users(text, limit=5)
        if not found:
            await update.message.reply_text("ملقيتش العميل ده. ابعت الـ ID بالأرقام:")
            return CREDIT_USER
        if len(found) > 1:
            lines = ["في أكتر من عميل مطابق، ابعت الـ ID بالظبط:"]
            for u in found:
                lines.append(f"• {u.get('username') or u.get('first_name')} — "
                             f"<code>{u['telegram_id']}</code>")
            await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
            return CREDIT_USER
        uid = found[0]["telegram_id"]

    context.user_data["credit_uid"] = uid
    u = db.get_user(uid)
    await update.message.reply_text(
        f"العميل: <code>{uid}</code>\nالرصيد الحالي: {ui.money(u['balance'] if u else 0)}\n\n"
        "اكتب المبلغ. رقم موجب = شحن، وبالسالب = خصم (مثال: -5).",
        parse_mode=ParseMode.HTML,
    )
    return CREDIT_AMOUNT


async def credit_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float(update.message.text.strip().replace(",", "."))
    except ValueError:
        await update.message.reply_text("لازم رقم. جرب تاني:")
        return CREDIT_AMOUNT
    if amount == 0:
        await update.message.reply_text("المبلغ مينفعش يكون صفر.")
        return CREDIT_AMOUNT

    uid = context.user_data.get("credit_uid")
    context.user_data["credit_amount"] = amount
    u = db.get_user(uid)
    current = u["balance"] if u else 0
    verb = "شحن" if amount > 0 else "خصم"
    await update.message.reply_text(
        f"📋 مراجعة\n👤 <code>{uid}</code>\n"
        f"💵 {verb}: {ui.money(abs(amount))}\n"
        f"💰 الرصيد: {ui.money(current)} ← {ui.money(current + amount)}",
        parse_mode=ParseMode.HTML,
        reply_markup=ui.kb([[ui.btn("✅ تأكيد", "adm:creditok"),
                             ui.btn("❌ إلغاء", "adm:creditno")]]),
    )
    return CREDIT_CONFIRM


async def credit_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "adm:creditno":
        context.user_data.pop("credit_uid", None)
        context.user_data.pop("credit_amount", None)
        await query.edit_message_text("اتلغى.", reply_markup=ui.kb([[ui.hub_btn()]]))
        return ConversationHandler.END

    uid = context.user_data.pop("credit_uid", None)
    amount = context.user_data.pop("credit_amount", None)
    if uid is None or amount is None:
        await query.edit_message_text("حصل خطأ، ابدأ تاني.", reply_markup=ui.kb([[ui.hub_btn()]]))
        return ConversationHandler.END

    admin_id = query.from_user.id
    if amount > 0:
        db.add_balance(uid, amount, kind="manual_credit", note="من الأدمن", admin_id=admin_id)
    else:
        ok = db.deduct_balance(uid, abs(amount), kind="manual_debit", note="من الأدمن",
                               admin_id=admin_id)
        if not ok:
            await query.edit_message_text("⚠️ رصيد العميل مش كفاية للخصم ده.",
                                          reply_markup=ui.kb([[ui.hub_btn()]]))
            return ConversationHandler.END

    db.log_action(admin_id, "manual_balance", f"{uid} {amount:+.2f}")
    new_balance = db.get_balance(uid)
    await query.edit_message_text(
        f"✅ تم.\n👤 <code>{uid}</code>\n💰 رصيده الحالي: {ui.money(new_balance)}",
        parse_mode=ParseMode.HTML,
        reply_markup=ui.kb([[ui.btn("👤 كارت العميل", f"srch:u:{uid}")], [ui.hub_btn()]]),
    )
    try:
        if amount > 0:
            await context.bot.send_message(
                uid, f"💰 تم شحن {ui.money(amount)} في رصيدك.\n"
                     f"رصيدك الحالي: {ui.money(new_balance)}"
            )
        else:
            await context.bot.send_message(
                uid, f"➖ اتخصم {ui.money(abs(amount))} من رصيدك.\n"
                     f"رصيدك الحالي: {ui.money(new_balance)}"
            )
    except Exception:
        await query.message.reply_text("⚠️ الرصيد اتعدل بس مقدرتش أبلغ العميل (غالبًا حاظر البوت).")
    return ConversationHandler.END


async def credit_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("credit_uid", None)
    context.user_data.pop("credit_amount", None)
    await update.message.reply_text("اتلغى.", reply_markup=ui.kb([[ui.hub_btn()]]))
    return ConversationHandler.END


credit_conv = ConversationHandler(
    entry_points=[
        CommandHandler("credit", credit_start),
        CallbackQueryHandler(credit_start, pattern=r"^adm:creditstart$"),
        CallbackQueryHandler(credit_for_user, pattern=r"^adm:credituser:\d+$"),
    ],
    states={
        CREDIT_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, credit_user)],
        CREDIT_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, credit_amount)],
        CREDIT_CONFIRM: [CallbackQueryHandler(credit_confirm, pattern=r"^adm:credit(ok|no)$")],
    },
    fallbacks=[CommandHandler("cancel", credit_cancel)],
)


# ---------------------------------------------------------------- warranty claims

@ui.require(perms.P_WARRANTY)
async def claims_panel(update: Update, context: ContextTypes.DEFAULT_TYPE, status="open"):
    claims = db.list_claims(status=status)
    label = {"open": "المفتوحة", "resolved": "المتحلة", "declined": "المرفوضة"}.get(status, status)
    tabs = [ui.btn("🟠 المفتوحة", "adm:claims:open"),
            ui.btn("✅ المتحلة", "adm:claims:resolved")]
    if not claims:
        await ui.reply(update, f"مفيش طلبات ضمان {label}.",
                       reply_markup=ui.kb([tabs, [ui.hub_btn()]]))
        return
    lines = [f"🛡️ <b>طلبات الضمان {label} ({len(claims)})</b>\n"]
    rows = []
    for c in claims:
        lines.append(f"#{c['id']} · طلب #{c['order_id']} · {ui.fmt_dt(c['created_at'], False)}")
        rows.append([ui.btn(f"🛡️ طلب ضمان #{c['id']}", f"adm:claim:{c['id']}")])
    rows.append(tabs)
    rows.append([ui.hub_btn()])
    await ui.reply(update, "\n".join(lines), reply_markup=ui.kb(rows), parse_mode=ParseMode.HTML)


@ui.require(perms.P_WARRANTY)
async def claim_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, claim_id: int):
    c = db.get_claim(claim_id)
    if not c:
        await ui.reply(update, "الطلب ده مش موجود.", reply_markup=ui.kb([[ui.hub_btn()]]))
        return
    o = db.order_detail(c["order_id"])
    text = (
        f"🛡️ <b>طلب ضمان #{c['id']}</b>\n"
        f"🏷️ الحالة: {ui.STATUS_AR.get(c['status'], c['status'])}\n"
        f"👤 العميل: <code>{c['user_id']}</code>\n"
        f"🧾 الطلب: #{c['order_id']} — {o.get('product_label') if o else '—'}\n"
        f"🔑 الشريحة: <code>{o.get('lpa_code') if o else '—'}</code>\n"
        f"{ui.warranty_text(o.get('warranty_until') if o else None)}\n"
        f"🕒 اتقدم: {ui.fmt_dt(c['created_at'])}\n\n"
        f"📝 <b>المشكلة</b>\n{c['reason']}"
    )
    if c["status"] != "open":
        text += f"\n\n✅ الحل: {c['resolution']} — {ui.fmt_dt(c['resolved_at'])}"
        rows = [[ui.btn("⬅️ رجوع", "adm:claims:open"), ui.hub_btn()]]
    else:
        rows = [
            [ui.btn("🔁 استبدال بشريحة جديدة", f"adm:cfix:{claim_id}")],
            [ui.btn("💵 استرجاع الفلوس للرصيد", f"adm:crefund:{claim_id}")],
            [ui.btn("❌ رفض الطلب", f"adm:cdecline:{claim_id}")],
            [ui.btn("👤 كارت العميل", f"srch:u:{c['user_id']}")],
            [ui.btn("⬅️ رجوع", "adm:claims:open"), ui.hub_btn()],
        ]
    await ui.reply(update, text, reply_markup=ui.kb(rows), parse_mode=ParseMode.HTML)


@ui.require(perms.P_WARRANTY)
async def claim_resolve(update: Update, context: ContextTypes.DEFAULT_TYPE, claim_id: int, how: str):
    query = update.callback_query
    c = db.get_claim(claim_id)
    if not c or c["status"] != "open":
        await query.answer("الطلب ده اتعالج قبل كده.", show_alert=True)
        return
    order = db.get_order(c["order_id"])
    admin_id = query.from_user.id

    if how == "replace":
        if db.count_available_stock(order["product_id"]) == 0:
            await query.answer("مفيش مخزون متاح للباقة دي.", show_alert=True)
            return
        new_order_id = db.create_order(
            c["user_id"], order["username"], order["product_id"], 0.0, "warranty",
            product_label_text=order.get("product_label") or "",
        )
        if not await handlers_user.deliver_sim(context, new_order_id):
            await query.answer("فشل التسليم، المخزون خلص.", show_alert=True)
            return
        new_order = db.get_order(new_order_id)
        db.resolve_claim(claim_id, "resolved", f"استبدال — طلب جديد #{new_order_id}",
                         admin_id, new_order.get("stock_id"))
        await context.bot.send_message(
            c["user_id"],
            f"🛡️ اتقبل طلب الضمان #{claim_id} واتبعتلك شريحة بديلة، شوف الرسالة اللي فوق 👆"
        )
        msg = f"✅ اتبعت شريحة بديلة في الطلب #{new_order_id}."

    elif how == "refund":
        amount = order["price"] or 0
        db.add_balance(c["user_id"], amount, kind="refund",
                       note=f"ضمان #{claim_id}", admin_id=admin_id,
                       ref_type="claim", ref_id=claim_id)
        db.update_order_status(order["id"], "refunded")
        db.resolve_claim(claim_id, "resolved", f"استرجاع {ui.money(amount)} للرصيد", admin_id)
        await context.bot.send_message(
            c["user_id"],
            f"🛡️ اتقبل طلب الضمان #{claim_id} واترجعلك {ui.money(amount)} في رصيدك.\n"
            f"💰 رصيدك الحالي: {ui.money(db.get_balance(c['user_id']))}"
        )
        msg = f"✅ اترجع {ui.money(amount)} لرصيد العميل."

    else:
        db.resolve_claim(claim_id, "declined", "اترفض", admin_id)
        await context.bot.send_message(
            c["user_id"],
            f"🛡️ للأسف اترفض طلب الضمان #{claim_id}. لو عندك استفسار كلم الدعم "
            f"{config.SUPPORT_CONTACT}"
        )
        msg = "❌ اترفض الطلب واتبلغ العميل."

    db.log_action(admin_id, "resolve_claim", f"{claim_id} {how}")
    await query.edit_message_text(
        msg, reply_markup=ui.kb([[ui.btn("⬅️ الطلبات", "adm:claims:open"), ui.hub_btn()]])
    )


# ---------------------------------------------------------------- broadcast

BCAST_TEXT, BCAST_CONFIRM = range(410, 412)


@ui.require(perms.P_BROADCAST)
async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
    total = len(db.get_all_user_ids())
    await ui.reply(
        update,
        f"📢 رسالة جماعية\n\nهتتبعت لـ {total} مستخدم (المحظورين مستثنيين).\n\n"
        "اكتب الرسالة:\n\n/cancel للإلغاء",
    )
    return BCAST_TEXT


async def broadcast_preview(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["bcast"] = update.message.text
    total = len(db.get_all_user_ids())
    await update.message.reply_text(
        f"معاينة:\n{'─' * 20}\n{update.message.text}\n{'─' * 20}\n\n"
        f"هتتبعت لـ {total} مستخدم. تأكيد؟",
        reply_markup=ui.kb([[ui.btn("✅ ابعت", "adm:bcastok"),
                             ui.btn("❌ إلغاء", "adm:bcastno")]]),
    )
    return BCAST_CONFIRM


async def broadcast_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "adm:bcastno":
        context.user_data.pop("bcast", None)
        await query.edit_message_text("اتلغى.", reply_markup=ui.kb([[ui.hub_btn()]]))
        return ConversationHandler.END

    text = context.user_data.pop("bcast", None)
    if not text:
        await query.edit_message_text("حصل خطأ، ابدأ تاني.", reply_markup=ui.kb([[ui.hub_btn()]]))
        return ConversationHandler.END

    user_ids = db.get_all_user_ids()
    await query.edit_message_text(f"⏳ جاري الإرسال لـ {len(user_ids)} مستخدم...")

    sent = blocked_count = failed = 0
    for uid in user_ids:
        try:
            await context.bot.send_message(uid, text)
            sent += 1
            db.mark_blocked_bot(uid, False)
        except Forbidden:
            blocked_count += 1
            db.mark_blocked_bot(uid, True)
        except Exception as e:
            failed += 1
            log.info("broadcast to %s failed: %s", uid, e)
        await asyncio.sleep(0.05)  # stay under Telegram's ~30 msg/sec

    db.log_action(query.from_user.id, "broadcast", f"sent {sent}")
    report = f"📢 خلص الإرسال.\n✅ وصلت: {sent}\n🙈 حاظرين البوت: {blocked_count}"
    if failed:
        report += f"\n❌ فشلت لأسباب تانية: {failed}"
    await query.message.reply_text(report, reply_markup=ui.kb([[ui.hub_btn()]]))
    return ConversationHandler.END


async def broadcast_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("bcast", None)
    await update.message.reply_text("اتلغى.", reply_markup=ui.kb([[ui.hub_btn()]]))
    return ConversationHandler.END


broadcast_conv = ConversationHandler(
    entry_points=[
        CommandHandler("broadcast", broadcast_start),
        CallbackQueryHandler(broadcast_start, pattern=r"^adm:bcaststart$"),
    ],
    states={
        BCAST_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_preview)],
        BCAST_CONFIRM: [CallbackQueryHandler(broadcast_send, pattern=r"^adm:bcast(ok|no)$")],
    },
    fallbacks=[CommandHandler("cancel", broadcast_cancel)],
)


# ---------------------------------------------------------------- settings

@ui.require(perms.P_SETTINGS)
async def settings_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    on = db.is_maintenance()
    text = (
        "⚙️ <b>الإعدادات</b>\n\n"
        f"🛠️ الصيانة: {'🔴 شغالة' if on else '🟢 مقفولة'}\n"
        f"💬 رسالة الصيانة:\n<i>{db.get_maintenance_message()}</i>\n\n"
        f"📄 رسالة التسليم:\n<i>{db.get_delivery_note()[:160]}...</i>\n\n"
        f"🕒 التوقيت: {config.TIMEZONE}\n"
        f"⚠️ تنبيه المخزون عند: {config.LOW_STOCK_THRESHOLD}\n"
        f"🛡️ الضمان الافتراضي: {config.DEFAULT_WARRANTY_DAYS} يوم\n"
        f"🔢 أقصى كمية في الطلب: {config.MAX_BULK_QTY}"
    )
    rows = [
        [ui.btn("🔴 فعّل الصيانة" if not on else "🟢 اقفل الصيانة", "adm:maint")],
        [ui.btn("✏️ رسالة الصيانة", "adm:maintmsg"),
         ui.btn("📄 رسالة التسليم", "adm:notemsg")],
        [ui.btn("📥 نسخة احتياطية", "adm:backup"), ui.btn("📜 سجل التصرفات", "adm:audit")],
    ]
    if perms.is_owner(update.effective_user.id):
        rows.append([ui.btn(config.ADMIN_STAFF, "adm:staff")])
    rows.append([ui.hub_btn()])
    await ui.reply(update, text, reply_markup=ui.kb(rows), parse_mode=ParseMode.HTML)


@ui.require(perms.P_SETTINGS)
async def toggle_maintenance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    on = db.is_maintenance()
    db.set_maintenance(not on)
    db.log_action(update.effective_user.id, "maintenance", str(not on))
    await settings_panel(update, context)


MAINT_MSG = 420


@ui.require(perms.P_SETTINGS)
async def maint_msg_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        f"الرسالة الحالية:\n{db.get_maintenance_message()}\n\n"
        "اكتب الرسالة الجديدة:\n/cancel للإلغاء"
    )
    return MAINT_MSG


async def maint_msg_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db.set_maintenance_message(update.message.text.strip())
    await update.message.reply_text(
        "✅ اتحدثت رسالة الصيانة.",
        reply_markup=ui.kb([[ui.btn("⚙️ الإعدادات", "adm:settings"), ui.hub_btn()]]),
    )
    return ConversationHandler.END


async def generic_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("اتلغى.", reply_markup=ui.kb([[ui.hub_btn()]]))
    return ConversationHandler.END


maint_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(maint_msg_start, pattern=r"^adm:maintmsg$")],
    states={MAINT_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, maint_msg_save)]},
    fallbacks=[CommandHandler("cancel", generic_cancel)],
)


NOTE_MSG = 421


@ui.require(perms.P_SETTINGS)
async def note_msg_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        "📄 <b>رسالة التسليم</b>\nدي بتتكتب تحت كل شريحة بتتباع.\n\n"
        f"الحالية:\n<code>{db.get_delivery_note()}</code>\n\n"
        "اكتب الرسالة الجديدة، أو «مسح» عشان تشيلها خالص.\n/cancel للإلغاء",
        parse_mode=ParseMode.HTML,
    )
    return NOTE_MSG


async def note_msg_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    db.set_delivery_note("" if text in ("مسح", "-") else text)
    db.log_action(update.effective_user.id, "delivery_note", text[:60])
    await update.message.reply_text(
        "✅ اتحدثت رسالة التسليم.",
        reply_markup=ui.kb([[ui.btn("⚙️ الإعدادات", "adm:settings"), ui.hub_btn()]]),
    )
    return ConversationHandler.END


note_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(note_msg_start, pattern=r"^adm:notemsg$")],
    states={NOTE_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, note_msg_save)]},
    fallbacks=[CommandHandler("cancel", generic_cancel)],
)


@ui.require(perms.P_SETTINGS)
async def audit_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows_db = db.list_audit(25)
    back = [[ui.btn("⚙️ الإعدادات", "adm:settings"), ui.hub_btn()]]
    if not rows_db:
        await ui.reply(update, "السجل فاضي.", reply_markup=ui.kb(back))
        return
    lines = ["📜 <b>آخر تصرفات الأدمن</b>\n"]
    for r in rows_db:
        lines.append(f"• <code>{r['admin_id']}</code> — {r['action']} — {r['details']} "
                     f"({ui.fmt_dt(r['created_at'])})")
    await ui.reply(update, "\n".join(lines), reply_markup=ui.kb(back), parse_mode=ParseMode.HTML)


@ui.require(perms.P_SETTINGS)
async def backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("جاري التجهيز...")
    chat_id = query.from_user.id

    stock = db.list_stock(limit=10000)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "country", "plan", "lpa_code", "status", "supplier", "cost",
                "warranty_days", "batch", "added_at", "sold_at", "buyer_id"])
    for s in stock:
        w.writerow([s["id"], s.get("country") or "", s.get("data_amount") or "", s["lpa_code"],
                    s["status"], s.get("supplier") or "", s.get("cost_price") or 0,
                    s.get("warranty_days") or 0, s.get("batch_id") or "",
                    ui.fmt_dt(s.get("added_at")), ui.fmt_dt(s.get("sold_at")),
                    s.get("buyer_id") or ""])
    f1 = io.BytesIO(buf.getvalue().encode("utf-8-sig"))
    f1.name = "stock.csv"
    await context.bot.send_document(chat_id, document=f1, caption="📦 المخزون")

    users = db.list_users(limit=10000, order="balance")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["telegram_id", "username", "balance", "joined_at", "is_banned"])
    for u in users:
        w.writerow([u["telegram_id"], u.get("username") or "", u["balance"],
                    ui.fmt_dt(u.get("joined_at")), u.get("is_banned") or 0])
    f2 = io.BytesIO(buf.getvalue().encode("utf-8-sig"))
    f2.name = "users.csv"
    await context.bot.send_document(chat_id, document=f2, caption="👥 العملاء والأرصدة",
                                    reply_markup=ui.kb([[ui.hub_btn()]]))
    db.log_action(chat_id, "backup", "csv export")


# ---------------------------------------------------------------- staff & permissions

STAFF_ASK_ID, STAFF_PICK = range(430, 432)


def _staff_text():
    rows = db.list_staff()
    lines = ["🔑 <b>الأدمن والصلاحيات</b>\n", "<b>المالكين (من ملف .env)</b>"]
    for oid in sorted(config.OWNER_IDS):
        lines.append(f"• 👑 <code>{oid}</code> — كل الصلاحيات")
    lines.append("\n<b>الأدمن المضافين</b>")
    if not rows:
        lines.append("مفيش حد لسه.")
    for r in rows:
        names = "، ".join(perms.PERM_LABELS[p] for p in perms.perms_of(r["user_id"])) or "—"
        lines.append(f"• {perms.ROLE_LABELS.get(r['role'], r['role'])} "
                     f"{r.get('username') or ''} (<code>{r['user_id']}</code>)\n  {names}")
    return "\n".join(lines)


def _staff_kb():
    rows = [[ui.btn("➕ إضافة أدمن", "adm:staffadd")]]
    for r in db.list_staff():
        rows.append([ui.btn(f"🗑️ حذف {r.get('username') or r['user_id']}",
                            f"adm:staffdel:{r['user_id']}")])
    rows.append([ui.btn("⚙️ الإعدادات", "adm:settings"), ui.hub_btn()])
    return ui.kb(rows)


async def staff_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not perms.is_owner(update.effective_user.id):
        return
    await ui.reply(update, _staff_text(), reply_markup=_staff_kb(), parse_mode=ParseMode.HTML)


async def staff_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not perms.is_owner(query.from_user.id):
        await query.answer("المالك بس اللي يقدر.", show_alert=True)
        return ConversationHandler.END
    await query.answer()
    await query.edit_message_text(
        "🔑 ابعت ID تليجرام للشخص اللي عايز تضيفه أدمن.\n"
        "(يقدر يجيبه من بوت زي @userinfobot)\n\n/cancel للإلغاء"
    )
    return STAFF_ASK_ID


def _perm_keyboard(selected):
    rows = []
    for p in perms.ALL_PERMS:
        if p == perms.P_STAFF:
            continue
        mark = "✅" if p in selected else "⬜"
        rows.append([ui.btn(f"{mark} {perms.PERM_LABELS[p]}", f"adm:perm:{p}")])
    rows.append([ui.btn("🧑‍💼 مدير (كل حاجة)", "adm:role:manager"),
                 ui.btn("🛒 بائع", "adm:role:seller")])
    rows.append([ui.btn("🧑‍💻 دعم", "adm:role:support"),
                 ui.btn("👀 مشاهدة", "adm:role:viewer")])
    rows.append([ui.btn("💾 حفظ", "adm:permsave"), ui.btn("❌ إلغاء", "adm:permcancel")])
    return ui.kb(rows)


async def staff_add_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("لازم ID رقمي. جرب تاني:")
        return STAFF_ASK_ID
    uid = int(text)
    if perms.is_owner(uid):
        await update.message.reply_text("ده مالك أصلاً وعنده كل الصلاحيات.")
        return ConversationHandler.END

    u = db.get_user(uid)
    context.user_data["staff_uid"] = uid
    context.user_data["staff_username"] = (u.get("username") if u else "") or ""
    context.user_data["staff_perms"] = set(perms.ROLE_PRESETS["support"])
    await update.message.reply_text(
        f"🔑 صلاحيات <code>{uid}</code>\n"
        "اضغط على أي صلاحية عشان تشغلها أو تقفلها، أو اختار دور جاهز، وبعدين احفظ.",
        reply_markup=_perm_keyboard(context.user_data["staff_perms"]),
        parse_mode=ParseMode.HTML,
    )
    return STAFF_PICK


async def staff_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    selected = context.user_data.get("staff_perms", set())

    if data == "adm:permcancel":
        for k in ("staff_uid", "staff_perms", "staff_username"):
            context.user_data.pop(k, None)
        await query.edit_message_text("اتلغى.", reply_markup=ui.kb([[ui.hub_btn()]]))
        return ConversationHandler.END

    if data == "adm:permsave":
        uid = context.user_data.pop("staff_uid", None)
        username = context.user_data.pop("staff_username", "")
        chosen = sorted(context.user_data.pop("staff_perms", set()))
        if not uid or not chosen:
            await query.edit_message_text("لازم تختار صلاحية واحدة على الأقل.",
                                          reply_markup=ui.kb([[ui.hub_btn()]]))
            return ConversationHandler.END
        role = "custom"
        for name, preset in perms.ROLE_PRESETS.items():
            if name != "owner" and sorted(preset) == chosen:
                role = name
        db.upsert_staff(uid, username, role, ",".join(chosen), query.from_user.id)
        db.log_action(query.from_user.id, "add_staff", f"{uid} {role}")
        try:
            await context.bot.send_message(
                uid, "🔑 اتضافت كأدمن في البوت.\nاكتب /admin عشان تفتح لوحة التحكم."
            )
        except Exception:
            pass
        await query.edit_message_text(_staff_text(), reply_markup=_staff_kb(),
                                      parse_mode=ParseMode.HTML)
        return ConversationHandler.END

    if data.startswith("adm:role:"):
        role = data.split(":")[2]
        selected = set(perms.ROLE_PRESETS.get(role, []))
    elif data.startswith("adm:perm:"):
        p = data.split(":")[2]
        selected.symmetric_difference_update({p})

    context.user_data["staff_perms"] = selected
    await query.edit_message_reply_markup(reply_markup=_perm_keyboard(selected))
    return STAFF_PICK


async def staff_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for k in ("staff_uid", "staff_perms", "staff_username"):
        context.user_data.pop(k, None)
    await update.message.reply_text("اتلغى.", reply_markup=ui.kb([[ui.hub_btn()]]))
    return ConversationHandler.END


staff_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(staff_add_start, pattern=r"^adm:staffadd$")],
    states={
        STAFF_ASK_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, staff_add_id)],
        STAFF_PICK: [CallbackQueryHandler(
            staff_pick, pattern=r"^adm:(perm:\w+|role:\w+|permsave|permcancel)$")],
    },
    fallbacks=[CommandHandler("cancel", staff_cancel)],
)


# ---------------------------------------------------------------- router

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    if not perms.is_staff(user_id):
        await query.answer("مش مسموحلك.", show_alert=True)
        return
    await query.answer()
    parts = query.data.split(":")
    action = parts[1]

    if action == "home":
        await panel(update, context)
    elif action == "customers":
        await customers(update, context)
    elif action == "topusers":
        await user_list(update, context, "balance")
    elif action == "newusers":
        await user_list(update, context, "new")
    elif action == "banned":
        await banned_list(update, context)
    elif action == "blockers":
        await blockers_list(update, context)
    elif action == "ban":
        await toggle_ban(update, context, int(parts[2]))
    elif action == "claims":
        await claims_panel(update, context, parts[2])
    elif action == "claim":
        await claim_detail(update, context, int(parts[2]))
    elif action == "cfix":
        await claim_resolve(update, context, int(parts[2]), "replace")
    elif action == "crefund":
        await claim_resolve(update, context, int(parts[2]), "refund")
    elif action == "cdecline":
        await claim_resolve(update, context, int(parts[2]), "decline")
    elif action == "settings":
        await settings_panel(update, context)
    elif action == "maint":
        await toggle_maintenance(update, context)
    elif action == "audit":
        await audit_view(update, context)
    elif action == "backup":
        await backup(update, context)
    elif action == "staff":
        await staff_panel(update, context)
    elif action == "staffdel":
        if not perms.is_owner(user_id):
            return
        uid = int(parts[2])
        db.remove_staff(uid)
        db.log_action(user_id, "remove_staff", str(uid))
        await query.edit_message_text(_staff_text(), reply_markup=_staff_kb(),
                                      parse_mode=ParseMode.HTML)
