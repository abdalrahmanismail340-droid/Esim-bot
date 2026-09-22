"""Customer refund requests and admin-approved Binance Pay payouts."""

import asyncio
import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters

import config
import db
import permissions as perms
import ui
from binance_transfer import BinanceTransfer

log = logging.getLogger(__name__)
ASK_AMOUNT, ASK_ID, ASK_REASON, CONFIRM = range(700, 704)


def _clear(context):
    for key in ("refund_amount", "refund_balance", "refund_address", "refund_network", "refund_reason"):
        context.user_data.pop(key, None)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await ui.guard(update):
        return ConversationHandler.END
    balance = db.get_balance(update.effective_user.id)
    if balance <= 0:
        await ui.reply(update, "💸 رصيدك الحالي صفر، مفيش مبلغ تقدر تسترجعه.")
        return ConversationHandler.END
    context.user_data["refund_balance"] = balance
    await ui.reply(update, f"💸 اكتب مبلغ الاسترجاع (رصيدك: {ui.money(balance)}).\n\n/cancel للإلغاء")
    return ASK_AMOUNT


async def amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        value = float(update.message.text.strip().replace(",", "."))
    except ValueError:
        await update.message.reply_text("اكتب مبلغ صحيح.")
        return ASK_AMOUNT
    balance = float(context.user_data.get("refund_balance", db.get_balance(update.effective_user.id)))
    if value <= 0 or value > balance:
        await update.message.reply_text(f"المبلغ لازم يكون أكبر من صفر وأقصاه {ui.money(balance)}.")
        return ASK_AMOUNT
    context.user_data["refund_amount"] = round(value, 8)
    await update.message.reply_text("ابعت Binance ID بتاعك (أرقام فقط) للتحويل.")
    return ASK_ID


async def binance_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    value = update.message.text.strip()
    if not value.isdigit() or not 5 <= len(value) <= 20:
        await update.message.reply_text("الـ Binance ID لازم يكون أرقام فقط.")
        return ASK_ID
    context.user_data["refund_address"] = value
    context.user_data["refund_network"] = "BINANCE_ID"
    await update.message.reply_text("اكتب سبب الاسترجاع، أو اكتب - للتخطي.")
    return ASK_REASON


async def reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["refund_reason"] = "" if update.message.text.strip() == "-" else update.message.text.strip()[:500]
    await update.message.reply_text(
        "📋 راجع طلب الاسترجاع:\n"
        f"💵 المبلغ: {ui.money(context.user_data['refund_amount'])}\n"
        f"🆔 Binance ID: <code>{context.user_data['refund_address']}</code>\n"
        f"📝 السبب: {context.user_data['refund_reason'] or '—'}",
        parse_mode=ParseMode.HTML,
        reply_markup=ui.kb([[ui.btn("✅ تأكيد الطلب", "u:refundok"), ui.btn("❌ إلغاء", "u:refundno")]]),
    )
    return CONFIRM


async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "u:refundno":
        _clear(context)
        await query.edit_message_text("اتلغى طلب الاسترجاع.")
        return ConversationHandler.END
    user = query.from_user
    amount_value = context.user_data.get("refund_amount")
    recipient_id = context.user_data.get("refund_address")
    request_id = db.create_refund_request(user.id, amount_value, recipient_id, "BINANCE_ID", context.user_data.get("refund_reason", ""))
    if not request_id:
        _clear(context)
        await query.edit_message_text("⚠️ الرصيد اتغير أو مش كافي. افتح الرصيد وحاول تاني.")
        return ConversationHandler.END
    _clear(context)
    await query.edit_message_text(f"✅ اتسجل طلب الاسترجاع #{request_id}. الأدمن هيراجعه ويحوله تلقائيًا.")
    await ui.notify_admins(
        context,
        f"💸 <b>طلب استرجاع جديد #{request_id}</b>\n👤 {ui.user_tag(user)}\n"
        f"💵 {ui.money(amount_value)} · 🆔 <code>{recipient_id}</code>",
        perm=perms.P_WALLET,
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _clear(context)
    await update.message.reply_text("اتلغى.")
    return ConversationHandler.END


refund_conv = ConversationHandler(
    entry_points=[CommandHandler("refund", start), CallbackQueryHandler(start, pattern=r"^u:refund$")],
    states={
        ASK_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, amount)],
        ASK_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, binance_id)],
        ASK_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, reason)],
        CONFIRM: [CallbackQueryHandler(confirm, pattern=r"^u:refund(ok|no)$")],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
)


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not perms.can(update.effective_user.id, perms.P_WALLET):
        return
    rows = [[ui.btn(f"💸 #{req['id']} — {ui.money(req['amount'])}", f"adm:refund:{req['id']}")]
            for req in db.list_refund_requests("pending")]
    rows.append([ui.hub_btn()])
    await ui.reply(update, f"💸 <b>طلبات الاسترجاع ({len(rows)-1})</b>", reply_markup=ui.kb(rows), parse_mode=ParseMode.HTML)


async def admin_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, request_id: int):
    if not perms.can(update.effective_user.id, perms.P_WALLET):
        return
    req = db.get_refund_request(request_id)
    if not req:
        await update.callback_query.answer("الطلب غير موجود", show_alert=True)
        return
    text = (f"💸 <b>طلب الاسترجاع #{req['id']}</b>\n👤 <code>{req['user_id']}</code>\n"
            f"💵 {ui.money(req['amount'])}\n🆔 Binance ID: <code>{req['address']}</code>\n"
            f"📝 {req['reason'] or '—'}\nالحالة: {req['status']}")
    rows = []
    if req["status"] == "pending":
        rows.append([ui.btn("✅ موافقة وتحويل تلقائي", f"adm:refundpay:{request_id}"), ui.btn("❌ رفض", f"adm:refundreject:{request_id}")])
    rows.append([ui.btn("⬅️ رجوع", "adm:refunds"), ui.hub_btn()])
    await update.callback_query.edit_message_text(text, reply_markup=ui.kb(rows), parse_mode=ParseMode.HTML)


async def approve(update: Update, context: ContextTypes.DEFAULT_TYPE, request_id: int):
    query = update.callback_query
    await query.answer("جاري التحويل...")
    if not db.mark_refund_processing(request_id, query.from_user.id):
        await query.edit_message_text("⚠️ الطلب اتعالج أو مش موجود.")
        return
    req = db.get_refund_request(request_id)
    result = await asyncio.to_thread(BinanceTransfer().payout_to_binance_id, req["address"], req["amount"], request_id)
    txid = result.get("id") or result.get("orderId") or result.get("requestId")
    if result.get("ok") and txid:
        db.complete_refund(request_id, txid)
        try:
            await context.bot.send_message(req["user_id"], f"✅ تم إرسال طلب استرجاع {ui.money(req['amount'])} إلى Binance.\n🧾 رقم الطلب: <code>{txid}</code>", parse_mode=ParseMode.HTML)
        except Exception:
            pass
        await query.edit_message_text(f"✅ Binance قبل طلب التحويل.\nرقم الطلب: <code>{txid}</code>", parse_mode=ParseMode.HTML, reply_markup=ui.kb([[ui.btn("⬅️ الطلبات", "adm:refunds"), ui.hub_btn()]]))
    else:
        db.fail_refund(request_id, str(result)[:500])
        await query.edit_message_text("❌ فشل التحويل وتم إرجاع المبلغ لرصيد العميل.", reply_markup=ui.kb([[ui.btn("⬅️ الطلبات", "adm:refunds"), ui.hub_btn()]]))


async def reject(update: Update, context: ContextTypes.DEFAULT_TYPE, request_id: int):
    query = update.callback_query
    await query.answer()
    req = db.reject_refund(request_id, query.from_user.id, "رفض الأدمن")
    if not req:
        await query.edit_message_text("⚠️ الطلب اتعالج قبل كده.")
        return
    await context.bot.send_message(req["user_id"], f"❌ تم رفض طلب الاسترجاع #{request_id}، ورجع المبلغ لرصيدك.")
    await query.edit_message_text("✅ اترفض الطلب ورجع الرصيد للعميل.", reply_markup=ui.kb([[ui.btn("⬅️ الطلبات", "adm:refunds"), ui.hub_btn()]]))
