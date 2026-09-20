"""
Everything the customer sees: catalogue with live SIM counts, bulk pricing,
instant purchase from wallet balance, their own eSIMs with warranty state,
and wallet top-up.
"""

import io
import logging
import re
from urllib.parse import quote
from uuid import uuid4

import qrcode
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
import permissions as perms
import ui
from binance_verify import get_transaction_by_id
from ocrspace_verify import extract_tx_ids_from_image

log = logging.getLogger(__name__)

AWAITING_TOPUP_TX = "awaiting_topup_tx"


# ---------------------------------------------------------------- start / help

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await ui.guard(update):
        return
    user = update.effective_user
    db.upsert_user(user.id, user.username, user.first_name)
    db.mark_blocked_bot(user.id, False)

    balance = db.get_balance(user.id)
    counts = db.stock_counts()
    countries = db.list_countries_with_stock()
    available_countries = sum(1 for c in countries if c["available"] > 0)

    await update.message.reply_text(
        f"أهلاً {user.first_name} 👋\n"
        "متجر شرايح eSIM بتفعيل فوري.\n\n"
        f"💰 رصيدك: {ui.money(balance)}\n"
        f"📦 متوفر دلوقتي: {counts['available']} شريحة في {available_countries} دولة\n\n"
        "اختار من القائمة تحت 👇",
        reply_markup=ui.main_menu_keyboard(user.id),
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🔥 <b>ازاي تشتري eSIM</b>\n\n"
        "1️⃣ اشحن رصيدك من 💳 شحن الرصيد (تحويل USDT على Binance Pay)\n"
        "2️⃣ ابعت رقم المعاملة أو سكرين شوت التحويل — الرصيد بيتضاف تلقائي\n"
        "3️⃣ ادخل 🌍 الشرايح المتاحة واختار الدولة والباقة والكمية\n"
        "4️⃣ لو رصيدك يكفي، الشرايح بتتسلم فورًا: كود LPA + QR\n\n"
        "🏷️ <b>أسعار الجملة</b>: كل ما تاخد كمية أكبر، سعر الشريحة يقل تلقائيًا.\n"
        "السعر بيظهرلك قبل ما تدفع.\n\n"
        "🛡️ <b>الضمان</b>: كل شريحة ليها مدة ضمان مكتوبة قبل الشراء. لو حصلت مشكلة "
        "وانت في مدة الضمان، ادخل 🧾 شرايحي واضغط «طلب ضمان».\n\n"
        f"🧑‍💻 الدعم: {config.SUPPORT_CONTACT}"
    )
    await ui.reply(update, text, parse_mode=ParseMode.HTML)


async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await ui.guard(update):
        return
    user = update.effective_user
    await update.message.reply_text(f"🧑‍💻 للتواصل مع الدعم الفني: {config.SUPPORT_CONTACT}")
    await ui.notify_admins(
        context,
        f"🧑‍💻 <b>عميل ضغط الدعم الفني</b>\n👤 {ui.user_tag(user)}\n"
        f"💰 رصيده: {ui.money(db.get_balance(user.id))}",
        perm=perms.P_USERS,
    )


# ---------------------------------------------------------------- catalogue

async def browse(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await ui.guard(update):
        return
    countries = db.list_countries_with_stock()
    if not countries:
        await ui.reply(update, "مفيش باقات متاحة دلوقتي، تابعنا قريب 🙏")
        return

    rows, total = [], 0
    for c in countries:
        total += c["available"]
        suffix = f"— {c['available']} شريحة" if c["available"] else "— نفذ"
        rows.append([ui.btn(f"{c['flag']} {c['name']} {suffix}", f"u:c:{c['id']}")])
    rows.append([ui.btn("🔄 تحديث", "u:browse")])
    text = (
        "🌍 <b>الدول المتاحة</b>\n"
        f"إجمالي الشرايح المتاحة دلوقتي: <b>{total}</b>\n\n"
        "اختار الدولة:"
    )
    await ui.reply(update, text, reply_markup=ui.kb(rows), parse_mode=ParseMode.HTML)


async def show_plans(update: Update, context: ContextTypes.DEFAULT_TYPE, country_id: int):
    country = db.get_country(country_id)
    plans = db.list_products_with_stock(country_id=country_id)
    if not country or not plans:
        await ui.reply(update, "مفيش باقات متاحة للدولة دي حاليًا.",
                       reply_markup=ui.kb([ui.back_btn("u:browse")]))
        return

    rows = []
    lines = [f"{country['flag']} <b>{country['name']}</b>\n"]
    for p in plans:
        avail = p["available"]
        state = f"✅ متاح {avail}" if avail else "❌ نفذ"
        tiers = db.list_tiers(p["id"])
        line = (f"• {p['data_amount']} / {p['validity_days']} يوم — "
                f"{ui.money(p['price_usdt'])} · {state}")
        if tiers:
            cheapest = min(t["unit_price"] for t in tiers)
            line += f"\n   🏷️ جملة: لحد {ui.money(cheapest)} للواحدة"
        lines.append(line)

        label = f"{p['data_amount']} · {p['validity_days']}ي · {ui.money(p['price_usdt'])} ({avail})"
        if not avail:
            label = f"❌ {p['data_amount']} · {p['validity_days']}ي (نفذ)"
        elif tiers:
            label = "🏷️ " + label
        rows.append([ui.btn(label, f"u:p:{p['id']}")])
    rows.append(ui.back_btn("u:browse"))
    await ui.reply(update, "\n".join(lines), reply_markup=ui.kb(rows), parse_mode=ParseMode.HTML)


def _qty_options(pid, avail):
    """1, plus every bulk tier the customer could actually reach right now."""
    opts = {1}
    for t in db.list_tiers(pid):
        if t["min_qty"] <= avail:
            opts.add(t["min_qty"])
    return sorted(q for q in opts if q <= avail)


async def plan_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int):
    p = db.get_product(product_id)
    if not p:
        await ui.reply(update, "الباقة دي مش موجودة.", reply_markup=ui.kb([ui.back_btn("u:browse")]))
        return
    avail = db.count_available_stock(product_id)
    warranty = p.get("warranty_days") or 0
    balance = db.get_balance(update.effective_user.id)
    tiers = db.list_tiers(product_id)

    text = (
        f"{p['flag']} <b>{p['country']}</b>\n"
        f"📶 الباقة: {p['data_amount']}\n"
        f"📅 المدة: {p['validity_days']} يوم\n"
        f"💵 السعر: {ui.money(p['price_usdt'])} للشريحة\n"
        f"🛡️ الضمان: {f'{warranty} يوم من وقت الشراء' if warranty else 'بدون ضمان'}\n"
        f"📦 المتاح: {avail} شريحة\n"
    )
    if p.get("daily_limit"):
        text += f"⚡ الليمت اليومي: {p['daily_limit']}\n"
    if tiers:
        text += "\n🏷️ <b>أسعار الجملة</b>\n"
        for t in tiers:
            save = (p["price_usdt"] - t["unit_price"]) * t["min_qty"]
            text += (f"• {t['min_qty']} شرايح فأكتر → {ui.money(t['unit_price'])} للواحدة"
                     + (f" (توفر {ui.money(save)})" if save > 0 else "") + "\n")
    if p.get("description"):
        text += f"\n{p['description']}\n"
    text += f"\n💰 رصيدك: {ui.money(balance)}"

    rows = []
    if avail:
        opts = _qty_options(product_id, avail)
        buttons = []
        for q in opts:
            unit, tier = db.unit_price_for(product_id, q)
            label = f"{q} شريحة — {ui.money(unit * q)}" if q > 1 else f"1 شريحة — {ui.money(unit)}"
            if tier:
                label = "🏷️ " + label
            buttons.append(ui.btn(label, f"u:conf:{product_id}:{q}"))
        for i in range(0, len(buttons), 2):
            rows.append(buttons[i:i + 2])
        if avail > 1:
            rows.append([ui.btn("✏️ كمية تانية", f"u:qty:{product_id}")])
    else:
        text += "\n\n❌ الباقة دي نفذت حاليًا."
    rows.append([ui.btn("⬅️ رجوع", f"u:c:{p['country_id']}")])
    await ui.reply(update, text, reply_markup=ui.kb(rows), parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------- confirm screen

def _confirm_view(user_id, product_id, qty):
    """Builds the 'you are about to pay X' screen. Returns (text, keyboard)."""
    p = db.get_product(product_id)
    if not p:
        return "الباقة دي مش موجودة.", ui.kb([ui.back_btn("u:browse")])

    avail = db.count_available_stock(product_id)
    if avail == 0:
        return "للأسف الباقة دي خلصت من المخزون.", ui.kb([ui.back_btn("u:browse")])
    qty = max(1, min(qty, avail, config.MAX_BULK_QTY))

    unit, tier = db.unit_price_for(product_id, qty)
    base = p["price_usdt"]
    total = unit * qty
    balance = db.get_balance(user_id)

    text = (
        "🛒 <b>تأكيد الطلب</b>\n\n"
        f"📦 {db.product_label(p)}\n"
        f"🔢 الكمية: <b>{qty}</b>\n"
        f"💵 سعر الشريحة: <b>{ui.money(unit)}</b>"
        + (f" <s>{ui.money(base)}</s> 🏷️" if tier else "") + "\n"
        f"💰 الإجمالي: <b>{ui.money(total)}</b>\n"
    )
    if tier:
        text += f"🎉 وفرت {ui.money((base - unit) * qty)} بسعر الجملة\n"
    text += f"\n💳 رصيدك: {ui.money(balance)}"

    if balance < total:
        text += f"\n⚠️ ناقصك {ui.money(total - balance)}"
        kb = ui.kb([
            [ui.btn("💳 شحن رصيد", "u:topup")],
            [ui.btn("⬅️ رجوع", f"u:p:{product_id}")],
        ])
    else:
        text += f" ← {ui.money(balance - total)} بعد الشراء"
        kb = ui.kb([
            [ui.btn(f"✅ ادفع {ui.money(total)}", f"u:go:{product_id}:{qty}")],
            [ui.btn("⬅️ رجوع", f"u:p:{product_id}")],
        ])
    return text, kb


async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int, qty: int):
    text, kb = _confirm_view(update.effective_user.id, product_id, qty)
    await ui.reply(update, text, reply_markup=kb, parse_mode=ParseMode.HTML)


ASK_QTY = 910


async def qty_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    pid = int(query.data.split(":")[2])
    avail = db.count_available_stock(pid)
    if not avail:
        await query.edit_message_text("الباقة دي نفذت.")
        return ConversationHandler.END
    context.user_data["qty_pid"] = pid
    limit = min(avail, config.MAX_BULK_QTY)
    await query.edit_message_text(
        f"✏️ اكتب عدد الشرايح اللي عايزها (من 1 لحد {limit}):\n\n/cancel للإلغاء"
    )
    return ASK_QTY


async def qty_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pid = context.user_data.get("qty_pid")
    if not pid:
        return ConversationHandler.END
    try:
        qty = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("لازم رقم. جرب تاني:")
        return ASK_QTY
    avail = db.count_available_stock(pid)
    limit = min(avail, config.MAX_BULK_QTY)
    if qty < 1 or qty > limit:
        await update.message.reply_text(f"الرقم لازم يكون من 1 لحد {limit}. جرب تاني:")
        return ASK_QTY

    context.user_data.pop("qty_pid", None)
    text, kb = _confirm_view(update.effective_user.id, pid, qty)
    await update.message.reply_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    return ConversationHandler.END


async def qty_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("qty_pid", None)
    await update.message.reply_text("اتلغى.")
    return ConversationHandler.END


qty_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(qty_start, pattern=r"^u:qty:\d+$")],
    states={ASK_QTY: [MessageHandler(filters.TEXT & ~filters.COMMAND, qty_save)]},
    fallbacks=[CommandHandler("cancel", qty_cancel),
               MessageHandler(ui.MENU_ESCAPE, qty_cancel)],
)


# ---------------------------------------------------------------- fulfillment

def _take_sim(order_id, product_id, p):
    """Pulls one SIM out of stock and attaches it to an order with its warranty."""
    sim = db.reserve_stock(product_id, order_id)
    if not sim:
        return None
    warranty_days = sim.get("warranty_days") or (p.get("warranty_days") if p else 0) or 0
    db.attach_stock_to_order(order_id, sim["id"], sim.get("cost_price") or 0,
                             ui.warranty_until_from(warranty_days))
    return sim


def activation_links(lpa_code):
    """The one-tap activation URLs. The LPA code is percent-encoded so the ':'
    and '$' inside it survive being a query parameter."""
    carddata = quote(lpa_code, safe="")
    return (
        f"https://esimsetup.apple.com/esim_qrcode_provisioning?carddata={carddata}",
        f"https://esimsetup.android.com/esim_qrcode_provisioning?carddata={carddata}",
    )


def esim_card(order_id, p, sim):
    """The delivery message: code, daily limit, one-tap activation links, footer."""
    lpa = sim["lpa_code"]
    ios_url, android_url = activation_links(lpa)
    days = sim.get("validity_days") or (p["validity_days"] if p else "")
    warranty_days = sim.get("warranty_days") or (p.get("warranty_days") if p else 0) or 0

    parts = [
        "📋 <b>بيانات eSIM</b>",
        "",
        f"📦 {db.product_label(p)}",
        f"🧾 الطلب: #{order_id}",
        "",
        f"📋 <b>Code:</b> <code>{lpa}</code>",
    ]
    if p and p.get("daily_limit"):
        parts += ["", f"⚡ الليمت اليومي: {p['daily_limit']}"]
    parts += [
        "",
        f"📅 صلاحية الباقة: {days} يوم",
        f"🛡️ الضمان: {warranty_days} يوم" if warranty_days else "🛡️ بدون ضمان",
        "",
        f'🍎 <a href="{ios_url}">تفعيل iPhone مباشرة</a>',
        f'🤖 <a href="{android_url}">تفعيل Android مباشرة</a>',
    ]
    note = db.get_delivery_note()
    if note:
        parts += ["", "━━━━━━━━━━━━━━━", "", note]
    return "\n".join(parts)


async def send_esim(context, user_id, order_id, p, sim):
    """QR image + the card. Telegram caps captions at 1024 characters, so a long
    footer is sent as a follow-up message instead of being cut off."""
    bio = io.BytesIO()
    bio.name = f"esim_{order_id}.png"
    qrcode.make(sim["lpa_code"]).save(bio, "PNG")
    bio.seek(0)

    card = esim_card(order_id, p, sim)
    if len(card) <= 1024:
        await context.bot.send_photo(user_id, photo=bio, caption=card,
                                     parse_mode=ParseMode.HTML)
    else:
        await context.bot.send_photo(user_id, photo=bio,
                                     caption=f"📋 بيانات eSIM — الطلب #{order_id}")
        await context.bot.send_message(user_id, card, parse_mode=ParseMode.HTML,
                                       disable_web_page_preview=True)


async def deliver_sim(context, order_id: int) -> bool:
    """Single-SIM delivery — used by the warranty replacement flow."""
    order = db.get_order(order_id)
    if not order:
        return False
    p = db.get_product(order["product_id"])
    sim = _take_sim(order_id, order["product_id"], p)
    if not sim:
        return False
    await send_esim(context, order["user_id"], order_id, p, sim)
    return True


async def _send_batch(context, user_id, p, delivered):
    """Bulk delivery. Small batches get a full card each; big ones get a compact
    list with the activation links plus a .txt of all the codes."""
    if len(delivered) <= config.QR_LIMIT:
        await context.bot.send_message(
            user_id, f"🎉 <b>اتسلمت {len(delivered)} شرايح</b>\n📦 {db.product_label(p)}",
            parse_mode=ParseMode.HTML,
        )
        for order_id, sim in delivered:
            await send_esim(context, user_id, order_id, p, sim)
        return

    lines = [f"🎉 <b>اتسلمت {len(delivered)} شريحة</b>", f"📦 {db.product_label(p)}", ""]
    for order_id, sim in delivered:
        ios_url, android_url = activation_links(sim["lpa_code"])
        lines.append(
            f"<b>#{order_id}</b>\n<code>{sim['lpa_code']}</code>\n"
            f'🍎 <a href="{ios_url}">تفعيل iPhone</a> · '
            f'🤖 <a href="{android_url}">تفعيل Android</a>'
        )
    note = db.get_delivery_note()
    if note:
        lines += ["", "━━━━━━━━━━━━━━━", "", note]

    # Telegram caps a message at 4096 chars — split on whole SIM blocks.
    chunk = []
    size = 0
    for block in lines:
        if size + len(block) > 3500 and chunk:
            await context.bot.send_message(user_id, "\n".join(chunk),
                                           parse_mode=ParseMode.HTML,
                                           disable_web_page_preview=True)
            chunk, size = [], 0
        chunk.append(block)
        size += len(block) + 1
    if chunk:
        await context.bot.send_message(user_id, "\n".join(chunk), parse_mode=ParseMode.HTML,
                                       disable_web_page_preview=True)

    text = "\n".join(sim["lpa_code"] for _oid, sim in delivered)
    doc = io.BytesIO(text.encode("utf-8"))
    doc.name = "esim_codes.txt"
    await context.bot.send_document(
        user_id, document=doc,
        caption="📄 كل الأكواد في ملف.\nتقدر تجيب الـ QR لأي شريحة من 🧾 شرايحي.",
    )


async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int, qty: int):
    query = update.callback_query
    user = query.from_user
    p = db.get_product(product_id)
    if not p:
        await query.edit_message_text("الباقة دي مش موجودة.")
        return

    avail = db.count_available_stock(product_id)
    if avail == 0:
        await query.edit_message_text("للأسف الباقة دي خلصت من المخزون، جرب باقة تانية.")
        return
    qty = max(1, min(qty, avail, config.MAX_BULK_QTY))

    unit, tier = db.unit_price_for(product_id, qty)
    total = unit * qty
    balance = db.get_balance(user.id)
    if balance < total:
        await query.edit_message_text(
            f"💰 رصيدك: {ui.money(balance)}\n"
            f"💵 المطلوب: {ui.money(total)}\n"
            f"➕ محتاج تشحن: {ui.money(total - balance)}",
            reply_markup=ui.kb([[ui.btn("💳 شحن رصيد", "u:topup")],
                                [ui.btn("⬅️ رجوع", f"u:p:{product_id}")]]),
        )
        return

    # unique per purchase, so two orders in the same second never share a group
    group_id = f"G-{ui.now_local().strftime('%y%m%d%H%M')}-{uuid4().hex[:6]}"
    if not db.deduct_balance(user.id, total, kind="purchase", note=group_id,
                             ref_type="order_group"):
        await query.edit_message_text("حصل خطأ في خصم الرصيد، حاول تاني.")
        return

    await query.edit_message_text(f"⏳ جاري تجهيز {qty} شريحة...")

    label = db.product_label(p)
    delivered, failed = [], 0
    for _ in range(qty):
        order_id = db.create_order(user.id, user.username or user.first_name, product_id,
                                   unit, "wallet", product_label_text=label, group_id=group_id)
        sim = _take_sim(order_id, product_id, p)
        if sim:
            delivered.append((order_id, sim))
        else:
            db.update_order_status(order_id, "refunded")
            failed += 1

    if failed:
        db.add_balance(user.id, unit * failed, kind="refund",
                       note=f"{group_id} — {failed} مش متاحة", ref_type="order_group")

    if not delivered:
        await query.edit_message_text("⚠️ المخزون خلص فجأة، واترجعلك المبلغ كامل لرصيدك.")
        await ui.notify_admins(
            context,
            f"⚠️ <b>بيع فشل — المخزون خلص</b>\n👤 {ui.user_tag(user)}\n📦 {label}\n"
            f"💵 اترجعله {ui.money(total)}",
            perm=perms.P_STOCK,
        )
        return

    if len(delivered) == 1:
        order_id, sim = delivered[0]
        await send_esim(context, user.id, order_id, p, sim)
    else:
        await _send_batch(context, user.id, p, delivered)

    sold = len(delivered)
    charged = unit * sold
    new_balance = db.get_balance(user.id)
    remaining = db.count_available_stock(product_id)

    summary = (f"✅ تم الشراء: {sold} شريحة بـ {ui.money(charged)}\n"
               f"💰 رصيدك الحالي: {ui.money(new_balance)}")
    if failed:
        summary += f"\n⚠️ {failed} شريحة مكانتش متاحة واترجع تمنها لرصيدك."
    summary += "\n\nتلاقي الشرايح والضمان بتاعها في 🧾 شرايحي."
    await context.bot.send_message(user.id, summary)

    cost = sum((sim.get("cost_price") or 0) for _oid, sim in delivered)
    await ui.notify_admins(
        context,
        f"🛒 <b>عملية بيع جديدة</b>\n👤 {ui.user_tag(user)}\n"
        f"📦 {label}\n"
        f"🔢 الكمية: {sold}" + (f" 🏷️ (سعر جملة من {tier})" if tier else "") + "\n"
        f"💵 {ui.money(unit)} للواحدة · الإجمالي {ui.money(charged)}\n"
        f"🏭 التكلفة: {ui.money(cost)} · 📈 الربح: {ui.money(charged - cost)}\n"
        f"💰 رصيده بعد الشراء: {ui.money(new_balance)}\n"
        f"📉 باقي في المخزون: {remaining}",
    )
    if remaining <= config.LOW_STOCK_THRESHOLD:
        await ui.notify_admins(
            context,
            f"⚠️ <b>المخزون قرب يخلص</b>\n{label}\nباقي {remaining} شريحة بس — ضيف ستوك.",
            perm=perms.P_STOCK,
        )


# ---------------------------------------------------------------- my eSIMs

async def my_esims(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await ui.guard(update):
        return
    orders = db.get_user_orders(update.effective_user.id)
    if not orders:
        await ui.reply(update, "لسه مشتريتش أي شريحة. ادخل 🌍 الشرايح المتاحة وابدأ 👌")
        return

    rows, lines = [], ["🧾 <b>شرايحي</b>\n"]
    for o in orders[:config.PAGE_SIZE * 2]:
        label = o.get("product_label") or (
            f"{o.get('flag') or ''} {o.get('country') or ''} — {o.get('data_amount') or ''}"
        )
        state = ui.STATUS_AR.get(o["status"], o["status"])
        warr = ui.warranty_text(o.get("warranty_until")) if o["status"] == "approved" else ""
        lines.append(f"#{o['id']} · {label} · {state}\n   {warr}".rstrip())
        if o["status"] == "approved":
            rows.append([ui.btn(f"#{o['id']} — {label[:28]}", f"u:o:{o['id']}")])
    await ui.reply(update, "\n".join(lines), reply_markup=ui.kb(rows) if rows else None,
                   parse_mode=ParseMode.HTML)


async def esim_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: int):
    query = update.callback_query
    o = db.order_detail(order_id)
    if not o or o["user_id"] != query.from_user.id:
        await query.answer("الطلب ده مش بتاعك.", show_alert=True)
        return

    text = (
        f"🧾 <b>الطلب #{o['id']}</b>\n"
        f"📦 {o.get('product_label') or db.product_label(o)}\n"
        f"💵 دفعت: {ui.money(o['price'])}\n"
        f"🕒 وقت الشراء: {ui.fmt_dt(o['created_at'])}\n"
        f"{ui.warranty_text(o.get('warranty_until'))}\n"
    )
    if o.get("lpa_code"):
        text += f"\n🔑 كود التفعيل:\n<code>{o['lpa_code']}</code>"

    rows = [[ui.btn("📷 ابعتلي الـ QR تاني", f"u:qr:{order_id}")]]
    left = ui.days_left(o.get("warranty_until"))
    if left is not None and left >= 0:
        rows.append([ui.btn("🛡️ طلب ضمان", f"u:wc:{order_id}")])
    rows.append(ui.back_btn("u:orders"))
    await query.edit_message_text(text, reply_markup=ui.kb(rows), parse_mode=ParseMode.HTML)


async def resend_qr(update: Update, context: ContextTypes.DEFAULT_TYPE, order_id: int):
    query = update.callback_query
    o = db.order_detail(order_id)
    if not o or o["user_id"] != query.from_user.id or not o.get("lpa_code"):
        await query.answer("مش قادر أجيب الكود ده.", show_alert=True)
        return
    p = db.get_product(o["product_id"])
    sim = db.get_stock(o["sim_id"]) if o.get("sim_id") else None
    await send_esim(context, query.from_user.id, order_id, p, sim or o)
    await query.answer("اتبعت ✅")


# ---------------------------------------------------------------- warranty claim

CLAIM_REASON = 900


async def claim_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    order_id = int(query.data.split(":")[2])
    o = db.order_detail(order_id)
    if not o or o["user_id"] != query.from_user.id:
        return ConversationHandler.END
    left = ui.days_left(o.get("warranty_until"))
    if left is None or left < 0:
        await query.edit_message_text("⌛ الضمان بتاع الشريحة دي انتهى.")
        return ConversationHandler.END

    context.user_data["claim_order"] = order_id
    await query.edit_message_text(
        f"🛡️ طلب ضمان للطلب #{order_id}\n\n"
        "اكتبلي المشكلة اللي حصلت بالتفصيل (مثلاً: الشريحة مش بتفعّل، النت مش شغال...).\n\n"
        "للإلغاء اكتب /cancel"
    )
    return CLAIM_REASON


async def claim_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    order_id = context.user_data.pop("claim_order", None)
    if not order_id:
        return ConversationHandler.END
    reason = update.message.text.strip()
    user = update.effective_user
    claim_id = db.create_claim(order_id, user.id, reason)
    await update.message.reply_text(
        f"✅ اتسجل طلب الضمان رقم #{claim_id}.\nهنراجعه ونرد عليك في أقرب وقت 🙏"
    )
    o = db.order_detail(order_id)
    await ui.notify_admins(
        context,
        f"🛡️ <b>طلب ضمان جديد #{claim_id}</b>\n👤 {ui.user_tag(user)}\n"
        f"🧾 الطلب: #{order_id} — {o.get('product_label') or ''}\n"
        f"🔑 الشريحة: <code>{o.get('lpa_code')}</code>\n"
        f"{ui.warranty_text(o.get('warranty_until'))}\n\n📝 {reason}",
        perm=perms.P_WARRANTY,
    )
    return ConversationHandler.END


async def claim_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("claim_order", None)
    await update.message.reply_text("اتلغى.")
    return ConversationHandler.END



# ================================================================ refund request

REFUND_AMOUNT, REFUND_ID_OR_ADDR, REFUND_REASON, REFUND_CONFIRM = range(920, 924)


async def refund_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await ui.guard(update):
        return
    user_id = update.effective_user.id
    balance = db.get_balance(user_id)
    if balance <= 0:
        await ui.reply(update, "رصيدك صفر، مفيش حاجة لاسترجاعها 😊")
        return ConversationHandler.END

    context.user_data["refund_uid"] = user_id
    text = (
        f"💳 <b>طلب استرجاع رصيد</b>\n\n"
        f"رصيدك الحالي: <b>{ui.money(balance)}</b>\n\n"
        "كام مبلغ بدك تسترجع؟\n"
        "(رقم، أو اكتب «كل» لاسترجاع الكل)\n\n"
        "/cancel للإلغاء"
    )
    await ui.reply(update, text, parse_mode=ParseMode.HTML)
    return REFUND_AMOUNT


async def refund_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = context.user_data.get("refund_uid")
    balance = db.get_balance(user_id)
    text = update.message.text.strip()

    if text.lower() in ("كل", "all"):
        amount = balance
    else:
        try:
            amount = float(text.replace(",", "."))
        except ValueError:
            await update.message.reply_text("لازم رقم. جرب تاني:")
            return REFUND_AMOUNT

    if amount <= 0 or amount > balance:
        await update.message.reply_text(f"المبلغ لازم يكون من 0.01 لحد {ui.money(balance)}. جرب تاني:")
        return REFUND_AMOUNT

    context.user_data["refund_amount"] = amount
    await update.message.reply_text(
        f"2️⃣ <b>Binance ID</b> أو <b>USDT Address</b>\n"
        f"(يوزرنيمك في بينانس أو محفظة USDT بتاعتك)\n\n"
        f"المبلغ {ui.money(amount)} هيتحول للعنوان ده.\n\n"
        "/cancel للإلغاء",
        parse_mode=ParseMode.HTML,
    )
    return REFUND_ID_OR_ADDR


async def refund_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    binance_id = update.message.text.strip()
    if not binance_id or len(binance_id) < 3:
        await update.message.reply_text("لازم تكون عنوان صحيح. جرب تاني:")
        return REFUND_ID_OR_ADDR

    context.user_data["refund_binance_id"] = binance_id
    await update.message.reply_text(
        f"3️⃣ ليه بدك تسترجع الفلوس؟\n"
        "(تفصيل سريع، اختياري)\n\n"
        "/cancel للإلغاء أو ابعت الرسالة للمتابعة",
    )
    return REFUND_REASON


async def refund_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reason = update.message.text.strip() if update.message.text else None

    user_id = context.user_data.pop("refund_uid")
    amount = context.user_data.pop("refund_amount")
    binance_id = context.user_data.pop("refund_binance_id")

    text = (
        f"📋 <b>مراجعة</b>\n\n"
        f"💳 المبلغ: {ui.money(amount)}\n"
        f"🆔 Binance ID: <code>{binance_id}</code>\n"
        f"💬 السبب: {reason or '—'}\n\n"
        f"هل التفاصيل صحيحة؟"
    )
    context.user_data["refund_reason"] = reason
    await update.message.reply_text(
        text,
        reply_markup=ui.kb([[ui.btn("✅ أرسل الطلب", "u:refundok"),
                             ui.btn("❌ إلغاء", "u:refundno")]]),
        parse_mode=ParseMode.HTML,
    )
    return REFUND_CONFIRM


async def refund_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "u:refundno":
        for k in ("refund_uid", "refund_amount", "refund_binance_id", "refund_reason"):
            context.user_data.pop(k, None)
        await query.edit_message_text("اتلغى.")
        return ConversationHandler.END

    user_id = query.from_user.id
    amount = context.user_data.pop("refund_amount", 0)
    binance_id = context.user_data.pop("refund_binance_id", "")
    reason = context.user_data.pop("refund_reason", "")

    req_id = db.create_refund_request(user_id, amount, binance_id=binance_id, reason=reason)
    if not req_id:
        await query.edit_message_text("حصل خطأ. حاول تاني.")
        return ConversationHandler.END

    await query.edit_message_text(
        f"✅ اتسجل طلب الاسترجاع #{req_id}.\n"
        f"الأدمن هيراجعه ويحول الفلوس في أقرب وقت 🙏"
    )
    await ui.notify_admins(
        context,
        f"💳 <b>طلب استرجاع رصيد جديد #{req_id}</b>\n"
        f"👤 {ui.user_tag(query.from_user)}\n"
        f"💵 المبلغ: {ui.money(amount)}\n"
        f"🆔 Binance ID: <code>{binance_id}</code>\n"
        f"💬 السبب: {reason or '—'}",
        perm=perms.P_WALLET,
    )
    return ConversationHandler.END


async def refund_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for k in ("refund_uid", "refund_amount", "refund_binance_id", "refund_reason"):
        context.user_data.pop(k, None)
    await update.message.reply_text("اتلغى.")
    return ConversationHandler.END


refund_conv = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(refund_start, pattern=r"^u:refund$"),
        MessageHandler(filters.Regex(f"^{re.escape(config.MENU_BALANCE)}$"), refund_start),
    ],
    states={
        REFUND_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, refund_amount)],
        REFUND_ID_OR_ADDR: [MessageHandler(filters.TEXT & ~filters.COMMAND, refund_id)],
        REFUND_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, refund_reason)],
        REFUND_CONFIRM: [CallbackQueryHandler(refund_confirm, pattern=r"^u:refund(ok|no)$")],
    },
    fallbacks=[CommandHandler("cancel", refund_cancel)],
)

claim_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(claim_start, pattern=r"^u:wc:\d+$")],
    states={CLAIM_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, claim_save)]},
    fallbacks=[CommandHandler("cancel", claim_cancel),
               MessageHandler(ui.MENU_ESCAPE, claim_cancel)],
)


# ---------------------------------------------------------------- wallet

async def wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await ui.guard(update):
        return
    user_id = update.effective_user.id
    balance = db.get_balance(user_id)
    history = db.get_user_ledger(user_id, limit=5)
    lines = [f"💰 <b>رصيدك: {ui.money(balance)}</b>"]
    if history:
        lines.append("\n📜 آخر الحركات:")
        for h in history:
            sign = "+" if h["amount"] > 0 else ""
            lines.append(
                f"• {ui.LEDGER_AR.get(h['kind'], h['kind'])} {sign}{ui.money(h['amount'])} — "
                f"{ui.fmt_dt(h['created_at'])}"
            )
    rows = [[ui.btn("💳 شحن رصيد", "u:topup")]]
    if balance > 0:
        rows.append([ui.btn("💸 استرجاع رصيد", "u:refund")])
    await ui.reply(update, "\n".join(lines), reply_markup=ui.kb(rows),
                   parse_mode=ParseMode.HTML)


async def topup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await ui.guard(update):
        return
    context.user_data[AWAITING_TOPUP_TX] = True
    text = (
        f"💳 حوّل أي مبلغ USDT على:\n`{config.BINANCE_PAY_ID}`\n\n"
        "بعد التحويل ابعت رقم/ID المعاملة هنا، أو ابعت سكرين شوت التحويل وهنقراه تلقائي.\n\n"
        "المبلغ بيتضاف لرصيدك تلقائيًا بعد التحقق."
    )
    await ui.reply(update, text, parse_mode=ParseMode.MARKDOWN)


async def _verify_and_credit(user_id: int, tx_id: str):
    """('credited', amount) / ('reused', None) / ('not_found', None)"""
    match = get_transaction_by_id(config.BINANCE_API_KEY, config.BINANCE_API_SECRET, tx_id)
    if not match:
        return "not_found", None
    amount = abs(float(match.get("amount", 0)))
    if not db.create_topup(user_id, tx_id, amount):
        return "reused", None
    return "credited", amount


async def _handle_candidates(update, context, candidates, from_image):
    user = update.effective_user
    if not config.AUTO_VERIFY:
        await update.message.reply_text(
            "📨 استلمنا رقم المعاملة، الأدمن هيراجعه ويشحنلك رصيدك في أقرب وقت."
        )
        await ui.notify_admins(
            context,
            f"💳 <b>طلب شحن يدوي</b>\n👤 {ui.user_tag(user)}\n"
            f"🧾 <code>{', '.join(candidates[:5])}</code>",
            perm=perms.P_WALLET,
        )
        return

    for tx_id in candidates[:6]:
        status, amount = await _verify_and_credit(user.id, tx_id)
        if status == "credited":
            balance = db.get_balance(user.id)
            await update.message.reply_text(
                f"✅ اتأكد التحويل واتضاف {ui.money(amount)} لرصيدك.\n"
                f"💰 رصيدك الحالي: {ui.money(balance)}"
            )
            await ui.notify_admins(
                context,
                f"💳 <b>شحن رصيد تلقائي</b>\n👤 {ui.user_tag(user)}\n"
                f"💵 {ui.money(amount)} · 🧾 <code>{tx_id}</code>\n"
                f"💰 رصيده: {ui.money(balance)}",
                perm=perms.P_WALLET,
            )
            return
        if status == "reused":
            await update.message.reply_text("⚠️ رقم المعاملة ده اتستخدم قبل كده.")
            return

    await update.message.reply_text(
        "❌ ملقيتش معاملة مطابقة. اتأكد من الرقم وابعته تاني، "
        f"أو كلم الدعم {config.SUPPORT_CONTACT}"
    )
    await ui.notify_admins(
        context,
        f"❌ <b>محاولة شحن فشلت</b>\n👤 {ui.user_tag(user)}\n"
        f"🧾 <code>{', '.join(candidates[:5]) if candidates else 'مفيش'}</code>\n"
        f"{'📷 من صورة' if from_image else '⌨️ كتابة'}",
        perm=perms.P_WALLET,
    )


async def topup_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Catch-all for free text: only consumes it when we're waiting for a tx id."""
    if await ui.guard(update):
        return
    if not context.user_data.get(AWAITING_TOPUP_TX):
        return
    context.user_data[AWAITING_TOPUP_TX] = False
    tx_id = update.message.text.strip()
    await update.message.reply_text("⏳ جاري التحقق من المعاملة...")
    await _handle_candidates(update, context, [tx_id], from_image=False)


async def topup_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await ui.guard(update):
        return
    if not context.user_data.get(AWAITING_TOPUP_TX):
        return
    if not config.OCRSPACE_API_KEY:
        await update.message.reply_text("قراءة الصور مش مفعّلة، ابعت رقم المعاملة كتابة من فضلك.")
        return

    await update.message.reply_text("⏳ جاري قراءة الصورة...")
    tg_file = await context.bot.get_file(update.message.photo[-1].file_id)
    bio = io.BytesIO()
    await tg_file.download_to_memory(bio)

    candidates = extract_tx_ids_from_image(config.OCRSPACE_API_KEY, bio.getvalue())
    if not candidates:
        await update.message.reply_text("مش لاقي رقم معاملة واضح في الصورة، ابعته كتابة من فضلك.")
        return
    context.user_data[AWAITING_TOPUP_TX] = False
    await _handle_candidates(update, context, candidates, from_image=True)


# ---------------------------------------------------------------- router

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await ui.guard(update):
        return
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    action = parts[1]

    if action in ("browse", "home"):
        await browse(update, context)
    elif action == "wallet":
        await wallet(update, context)
    elif action == "topup":
        await topup(update, context)
    elif action == "orders":
        await my_esims(update, context)
    elif action == "help":
        await help_cmd(update, context)
    elif action == "c":
        await show_plans(update, context, int(parts[2]))
    elif action == "p":
        await plan_detail(update, context, int(parts[2]))
    elif action == "conf":
        await confirm(update, context, int(parts[2]), int(parts[3]))
    elif action == "go":
        await buy(update, context, int(parts[2]), int(parts[3]))
    elif action == "o":
        await esim_detail(update, context, int(parts[2]))
    elif action == "qr":
        await resend_qr(update, context, int(parts[2]))
