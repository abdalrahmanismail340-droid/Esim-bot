"""
Stock (SIM) management.

Adding stock is a guided wizard: pick the country, pick the package, then the bot
asks you for the supplier, the cost you paid, the validity days and the warranty
days before it takes the LPA codes. Those four answers are what make the search
and the profit reports actually useful.
"""

import io
import logging

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

log = logging.getLogger(__name__)

P = perms.P_STOCK
SKIP_WORDS = ("تخطي", "-", "لا", "مفيش")


# ---------------------------------------------------------------- SIM card

def sim_card(sid: int) -> str:
    """The full story of one SIM — used by the stock panel and by search."""
    s = db.stock_detail(sid)
    if not s:
        return "❌ الشريحة دي مش موجودة."

    lines = [
        f"🔎 <b>شريحة #{s['id']}</b>",
        f"📦 الباقة: {s.get('flag') or ''} {s.get('country') or '—'} — "
        f"{s.get('data_amount') or '—'} / {s.get('validity_days') or s.get('plan_days') or '—'} يوم",
        f"🔑 الكود: <code>{s['lpa_code']}</code>",
        f"🏷️ الحالة: {ui.STATUS_AR.get(s.get('status'), s.get('status'))}",
        f"🏭 المورد: {s.get('supplier') or '—'}",
        f"💵 التكلفة: {ui.money(s.get('cost_price'))}",
        f"📅 اتضافت: {ui.fmt_dt(s.get('added_at'))}",
    ]
    if s.get("batch_id"):
        lines.append(f"🗃️ الدفعة: {s['batch_id']}")

    if s.get("status") == "sold":
        buyer = s.get("buyer_name") or "—"
        lines += [
            "",
            "👤 <b>المشتري</b>",
            f"• {('@' + buyer) if buyer != '—' else '—'} (<code>{s.get('buyer_id')}</code>)",
            f"🕒 وقت البيع: {ui.fmt_dt(s.get('sold_at') or s.get('order_at'))}",
            f"🧾 الطلب: #{s.get('order_no')}",
            f"💵 سعر البيع: {ui.money(s.get('sold_price'))}",
            f"📈 الربح: {ui.money((s.get('sold_price') or 0) - (s.get('cost_price') or 0))}",
            f"{ui.warranty_text(s.get('warranty_until'))}",
        ]
    else:
        warr = s.get("warranty_days") or 0
        lines.append(f"🛡️ الضمان لما تتباع: {warr} يوم")
    if s.get("note"):
        lines.append(f"📝 ملاحظة: {s['note']}")
    return "\n".join(lines)


def sim_buttons(sid: int, back="stk:home"):
    s = db.stock_detail(sid)
    rows = []
    if s and s.get("status") == "available":
        rows.append([ui.btn("⚫ إلغاء الشريحة", f"stk:void:{sid}"),
                     ui.btn("🗑️ حذف", f"stk:del:{sid}")])
    if s and s.get("order_no"):
        rows.append([ui.btn(f"🧾 عرض الطلب #{s['order_no']}", f"srch:o:{s['order_no']}")])
    rows.append([ui.btn("⬅️ رجوع", back), ui.hub_btn()])
    return ui.kb(rows)


# ---------------------------------------------------------------- panel

@ui.require(P)
async def panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    counts = db.stock_counts()
    value = db.inventory_value()
    low = db.low_stock_products(config.LOW_STOCK_THRESHOLD)

    text = (
        "📦 <b>المخزون</b>\n\n"
        f"🟢 متاحة: <b>{counts['available']}</b>\n"
        f"🔴 مباعة: {counts['sold']}\n"
        f"⚫ ملغية: {counts['void']}\n"
        f"📊 الإجمالي: {counts['total']}\n\n"
        f"💵 تكلفة المخزون الحالي: {ui.money(value['cost_value'])}\n"
        f"🏷️ لو اتباع كله: {ui.money(value['sale_value'])} "
        f"(ربح متوقع {ui.money(value['sale_value'] - value['cost_value'])})"
    )
    if low:
        text += f"\n\n⚠️ <b>{len(low)} باقة قربت تخلص</b> — شوف «قرب يخلص»."

    rows = [
        [ui.btn("➕ إضافة ستوك", "stk:addnew")],
        [ui.btn("📋 عرض حسب الباقة", "stk:bycountry"), ui.btn("🏭 الموردين", "stk:suppliers")],
        [ui.btn("⚠️ قرب يخلص", "stk:low"), ui.btn("🔍 بحث عن شريحة", "srch:start")],
        [ui.hub_btn()],
    ]
    await ui.reply(update, text, reply_markup=ui.kb(rows), parse_mode=ParseMode.HTML)


@ui.require(P)
async def low_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows_db = db.low_stock_products(config.LOW_STOCK_THRESHOLD)
    if not rows_db:
        await ui.reply(update, "✅ كل الباقات عندها مخزون كويس.",
                       reply_markup=ui.kb([[ui.btn("⬅️ رجوع", "stk:home"), ui.hub_btn()]]))
        return
    lines = [f"⚠️ <b>باقات قربت تخلص</b> (أقل من أو يساوي {config.LOW_STOCK_THRESHOLD})\n"]
    kb_rows = []
    for p in rows_db:
        lines.append(f"• {db.product_label(p)} — متاح {p['available']}")
        kb_rows.append([ui.btn(f"➕ ستوك لـ {p['country']} {p['data_amount']}", f"stk:add:{p['id']}")])
    kb_rows.append([ui.btn("⬅️ رجوع", "stk:home"), ui.hub_btn()])
    await ui.reply(update, "\n".join(lines), reply_markup=ui.kb(kb_rows), parse_mode=ParseMode.HTML)


@ui.require(P)
async def suppliers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows_db = db.list_suppliers()
    if not rows_db:
        await ui.reply(update, "مفيش موردين متسجلين لسه.",
                       reply_markup=ui.kb([[ui.btn("⬅️ رجوع", "stk:home"), ui.hub_btn()]]))
        return
    lines = ["🏭 <b>الموردين</b>\n"]
    for r in rows_db:
        lines.append(
            f"• <b>{r['supplier']}</b> — إجمالي {r['total']} · متاح {r['available']} · "
            f"مباع {r['sold']} · مصروف {ui.money(r['spent'])}"
        )
    await ui.reply(update, "\n".join(lines),
                   reply_markup=ui.kb([[ui.btn("⬅️ رجوع", "stk:home"), ui.hub_btn()]]),
                   parse_mode=ParseMode.HTML)


@ui.require(P)
async def by_country(update: Update, context: ContextTypes.DEFAULT_TYPE, cid=None):
    if cid is None:
        countries = db.list_countries(active_only=False)
        rows = [[ui.btn(f"{c['flag']} {c['name']}", f"stk:cc:{c['id']}")] for c in countries]
        rows.append([ui.btn("⬅️ رجوع", "stk:home"), ui.hub_btn()])
        await ui.reply(update, "اختار الدولة:", reply_markup=ui.kb(rows))
        return
    plans = db.list_products_with_stock(country_id=cid, active_only=False)
    rows = [[ui.btn(
        f"{p['data_amount']} · {p['validity_days']}ي — متاح {p['available']} / مباع {p['sold']}",
        f"stk:list:{p['id']}:0")] for p in plans]
    rows.append([ui.btn("⬅️ رجوع", "stk:bycountry"), ui.hub_btn()])
    await ui.reply(update, "اختار الباقة:", reply_markup=ui.kb(rows))


@ui.require(P)
async def list_stock(update: Update, context: ContextTypes.DEFAULT_TYPE, pid: int, page: int):
    p = db.get_product(pid)
    items = db.list_stock(product_id=pid, limit=500)
    if not items:
        await ui.reply(update, "مفيش شرايح متسجلة للباقة دي.",
                       reply_markup=ui.kb([[ui.btn("➕ إضافة ستوك", f"stk:add:{pid}")],
                                           [ui.btn("⬅️ رجوع", "stk:bycountry"), ui.hub_btn()]]))
        return
    chunk, total_pages = ui.paginate(items, page)
    lines = [f"📦 <b>{db.product_label(p)}</b>\nإجمالي {len(items)} شريحة\n"]
    rows = []
    for s in chunk:
        lines.append(
            f"#{s['id']} · {ui.mask_code(s['lpa_code'])} · "
            f"{ui.STATUS_AR.get(s['status'], s['status'])} · {s.get('supplier') or '—'}"
        )
        rows.append([ui.btn(f"#{s['id']} {ui.mask_code(s['lpa_code'], 4)}", f"stk:sim:{s['id']}")])
    pager = ui.pager_row(f"stk:list:{pid}:", page, total_pages)
    if pager:
        rows.append(pager)
    rows.append([ui.btn("➕ إضافة ستوك", f"stk:add:{pid}")])
    rows.append([ui.btn("⬅️ رجوع", "stk:bycountry"), ui.hub_btn()])
    await ui.reply(update, "\n".join(lines), reply_markup=ui.kb(rows), parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------- add wizard

PICK_COUNTRY, PICK_PLAN, ASK_SUPPLIER, ASK_COST, ASK_DAYS, ASK_WARRANTY, ASK_CODES, ASK_CONFIRM = \
    range(700, 708)


@ui.require(P)
async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry from '➕ إضافة ستوك' — pick a country first."""
    query = update.callback_query
    await query.answer()
    countries = db.list_countries(active_only=False)
    if not countries:
        await query.edit_message_text("ضيف دولة وباقة الأول من 🗂️ الدول والباقات.")
        return ConversationHandler.END
    rows = [[ui.btn(f"{c['flag']} {c['name']}", f"stk:ac:{c['id']}")] for c in countries]
    await query.edit_message_text("📦 إضافة ستوك\n\n1️⃣ اختار الدولة:", reply_markup=ui.kb(rows))
    return PICK_COUNTRY


async def add_pick_country(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cid = int(query.data.split(":")[2])
    plans = db.list_products_with_stock(country_id=cid, active_only=False)
    if not plans:
        await query.edit_message_text(
            "الدولة دي مفيهاش باقات. ضيف باقة الأول.",
            reply_markup=ui.kb([[ui.btn("➕ إضافة باقة", f"cat:addplan:{cid}")]]),
        )
        return ConversationHandler.END
    rows = [[ui.btn(
        f"{p['data_amount']} · {p['validity_days']}ي · {ui.money(p['price_usdt'])} (متاح {p['available']})",
        f"stk:ap:{p['id']}")] for p in plans]
    await query.edit_message_text("2️⃣ اختار الباقة:", reply_markup=ui.kb(rows))
    return PICK_PLAN


async def _ask_supplier(message_or_query, context, pid):
    p = db.get_product(pid)
    context.user_data["stk"] = {
        "product_id": pid,
        "plan_days": p["validity_days"],
        "plan_warranty": p.get("warranty_days") or config.DEFAULT_WARRANTY_DAYS,
        "label": db.product_label(p),
    }
    text = (
        f"📦 {db.product_label(p)}\n"
        f"المتاح حاليًا: {db.count_available_stock(pid)}\n\n"
        "3️⃣ اسم المورد اللي جبت منه الشرايح دي؟\n"
        "(اكتب الاسم، أو «تخطي» لو مش عايز تسجله)\n\n/cancel للإلغاء"
    )
    if hasattr(message_or_query, "edit_message_text"):
        await message_or_query.edit_message_text(text)
    else:
        await message_or_query.reply_text(text)
    return ASK_SUPPLIER


async def add_pick_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    pid = int(query.data.split(":")[2])
    return await _ask_supplier(query, context, pid)


@ui.require(P)
async def add_for_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shortcut entry 'stk:add:<pid>' used from the low-stock list and after
    creating a package."""
    query = update.callback_query
    await query.answer()
    pid = int(query.data.split(":")[2])
    if not db.get_product(pid):
        await query.edit_message_text("الباقة دي مش موجودة.")
        return ConversationHandler.END
    return await _ask_supplier(query, context, pid)


async def add_supplier(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    context.user_data["stk"]["supplier"] = "" if text in SKIP_WORDS else text
    await update.message.reply_text(
        "4️⃣ التكلفة للشريحة الواحدة بالدولار (اللي دفعتها للمورد).\n"
        "اكتب رقم مثل 3.5، أو «تخطي» لو مش عايز تسجلها.\n"
        "⚠️ من غيرها التقارير هتوريك المبيعات بس من غير صافي ربح."
    )
    return ASK_COST


async def add_cost(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text in SKIP_WORDS:
        cost = 0.0
    else:
        try:
            cost = float(text.replace(",", "."))
        except ValueError:
            await update.message.reply_text("لازم رقم. جرب تاني:")
            return ASK_COST
    context.user_data["stk"]["cost_price"] = cost
    default_days = context.user_data["stk"]["plan_days"]
    await update.message.reply_text(
        f"5️⃣ الشرايح دي بتشتغل كام يوم؟\n"
        f"اكتب رقم، أو «تخطي» عشان تاخد مدة الباقة ({default_days} يوم)."
    )
    return ASK_DAYS


async def add_days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    data = context.user_data["stk"]
    if text in SKIP_WORDS:
        days = data["plan_days"]
    else:
        try:
            days = int(text)
        except ValueError:
            await update.message.reply_text("لازم رقم صحيح. جرب تاني:")
            return ASK_DAYS
    data["validity_days"] = days
    await update.message.reply_text(
        f"6️⃣ مدة الضمان بالأيام للشرايح دي؟\n"
        f"اكتب رقم، أو «تخطي» للافتراضي ({data['plan_warranty']} يوم)، أو 0 لو من غير ضمان."
    )
    return ASK_WARRANTY


async def add_warranty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    data = context.user_data["stk"]
    if text in SKIP_WORDS:
        warranty = data["plan_warranty"]
    else:
        try:
            warranty = int(text)
        except ValueError:
            await update.message.reply_text("لازم رقم صحيح. جرب تاني:")
            return ASK_WARRANTY
    data["warranty_days"] = warranty
    await update.message.reply_text(
        "7️⃣ ابعت أكواد التفعيل (LPA) — كل كود في سطر لوحده.\n"
        "تقدر كمان ترفع ملف .txt فيه الأكواد."
    )
    return ASK_CODES


def _summary(data, codes):
    return (
        "📋 <b>مراجعة الدفعة</b>\n"
        f"📦 {data['label']}\n"
        f"🏭 المورد: {data['supplier'] or '—'}\n"
        f"💵 التكلفة للواحدة: {ui.money(data['cost_price'])}\n"
        f"📅 مدة الشريحة: {data['validity_days']} يوم\n"
        f"🛡️ الضمان: {data['warranty_days']} يوم\n"
        f"🔢 عدد الأكواد: <b>{len(codes)}</b>\n"
        f"💰 إجمالي التكلفة: {ui.money(data['cost_price'] * len(codes))}"
    )


async def add_codes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = context.user_data.get("stk")
    if not data:
        return ConversationHandler.END

    if update.message.document:
        tg_file = await context.bot.get_file(update.message.document.file_id)
        bio = io.BytesIO()
        await tg_file.download_to_memory(bio)
        raw = bio.getvalue().decode("utf-8", errors="ignore")
    else:
        raw = update.message.text or ""

    codes = [line.strip() for line in raw.splitlines() if line.strip()]
    if not codes:
        await update.message.reply_text("محتاج كود واحد على الأقل. جرب تاني:")
        return ASK_CODES

    data["codes"] = codes
    await update.message.reply_text(
        _summary(data, codes),
        parse_mode=ParseMode.HTML,
        reply_markup=ui.kb([[ui.btn("✅ حفظ", "stk:save"), ui.btn("❌ إلغاء", "stk:cancel")]]),
    )
    return ASK_CONFIRM


async def add_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "stk:cancel":
        context.user_data.pop("stk", None)
        await query.edit_message_text("اتلغى.")
        return ConversationHandler.END

    data = context.user_data.pop("stk", None)
    if not data:
        await query.edit_message_text("حصل خطأ، ابدأ تاني.")
        return ConversationHandler.END

    batch = f"B-{ui.now_local().strftime('%y%m%d-%H%M')}"
    added, dupes = db.add_stock_batch(
        data["product_id"], data["codes"],
        supplier=data["supplier"], cost_price=data["cost_price"],
        validity_days=data["validity_days"], warranty_days=data["warranty_days"],
        batch_id=batch, added_by=query.from_user.id,
    )
    db.log_action(query.from_user.id, "add_stock",
                  f"product {data['product_id']} +{added} batch {batch}")

    available = db.count_available_stock(data["product_id"])
    msg = (
        f"✅ اتضاف <b>{added}</b> شريحة لـ {data['label']}\n"
        f"🗃️ الدفعة: <code>{batch}</code>\n"
        f"📦 المتاح دلوقتي: <b>{available}</b>\n"
        f"💰 تكلفة الدفعة: {ui.money(data['cost_price'] * added)}"
    )
    if dupes:
        msg += f"\n\n⚠️ اتخطى {len(dupes)} كود لأنهم موجودين قبل كده:\n<code>" + \
               "\n".join(ui.mask_code(c) for c in dupes[:5]) + "</code>"
    await query.edit_message_text(
        msg, parse_mode=ParseMode.HTML,
        reply_markup=ui.kb([[ui.btn("📦 المخزون", "stk:home")],
                            [ui.btn("➕ دفعة تانية", f"stk:add:{data['product_id']}")]]),
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("stk", None)
    await update.message.reply_text("اتلغى.")
    return ConversationHandler.END


addstock_conv = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(add_start, pattern=r"^stk:addnew$"),
        CallbackQueryHandler(add_for_plan, pattern=r"^stk:add:\d+$"),
    ],
    states={
        PICK_COUNTRY: [CallbackQueryHandler(add_pick_country, pattern=r"^stk:ac:\d+$")],
        PICK_PLAN: [CallbackQueryHandler(add_pick_plan, pattern=r"^stk:ap:\d+$")],
        ASK_SUPPLIER: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_supplier)],
        ASK_COST: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_cost)],
        ASK_DAYS: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_days)],
        ASK_WARRANTY: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_warranty)],
        ASK_CODES: [MessageHandler((filters.TEXT & ~filters.COMMAND) | filters.Document.ALL, add_codes)],
        ASK_CONFIRM: [CallbackQueryHandler(add_confirm, pattern=r"^stk:(save|cancel)$")],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
)


# ---------------------------------------------------------------- router

@ui.require(P)
async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    action = parts[1]

    if action == "home":
        await panel(update, context)
    elif action == "low":
        await low_stock(update, context)
    elif action == "suppliers":
        await suppliers(update, context)
    elif action == "bycountry":
        await by_country(update, context)
    elif action == "cc":
        await by_country(update, context, int(parts[2]))
    elif action == "list":
        await list_stock(update, context, int(parts[2]), int(parts[3]))
    elif action == "sim":
        sid = int(parts[2])
        await query.edit_message_text(sim_card(sid), reply_markup=sim_buttons(sid),
                                      parse_mode=ParseMode.HTML)
    elif action == "void":
        sid = int(parts[2])
        db.void_stock(sid, note="ألغاها الأدمن")
        db.log_action(query.from_user.id, "void_stock", str(sid))
        await query.edit_message_text(sim_card(sid), reply_markup=sim_buttons(sid),
                                      parse_mode=ParseMode.HTML)
    elif action == "del":
        sid = int(parts[2])
        db.delete_stock(sid)
        db.log_action(query.from_user.id, "delete_stock", str(sid))
        await query.edit_message_text("🗑️ اتحذفت الشريحة من المخزون.",
                                      reply_markup=ui.kb([[ui.btn("⬅️ المخزون", "stk:home"),
                                                           ui.hub_btn()]]))
